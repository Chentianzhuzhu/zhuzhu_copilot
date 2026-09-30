"""多会话并发性能探针：验证连接复用池在 3 路会话并发长任务下不成为串行瓶颈。

用法：python scripts/_probe_concurrency_perf.py [--sessions 3] [--rounds 3]
真实调用 settings.json 配置的模型接口（每轮只要求极短回复，成本极低）。

测量（阶段 3b 验收基线）：
  * 实际新建连接数（探针侧计数 agent_http._new_conn）——复用的直接证据：
    多路会话并发且每路多轮时，新建连接数应 ≈ 会话数（每路一条 keep-alive 连接）；
  * 每路会话逐轮延迟：第 1 轮含 TCP/TLS 握手，后续轮应显著更快；
  * 并发墙钟 vs Σ延迟：墙钟明显小于 Σ 延迟说明请求未串行化（池锁不阻塞传输）。
"""
import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zhuzhu_Copilot.core import agent_http, agent_llm                  # noqa: E402

PROMPT = "只回复两个字：好的"     # 极短请求：延迟主项是连接与首包，便于观察复用收益


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=3)
    ap.add_argument("--rounds", type=int, default=3)
    args = ap.parse_args()

    cfg = agent_llm.load_model_config()
    base_url = (cfg.get("base_url") or "").strip()
    if not base_url:
        print("未配置模型接口（settings.json 无 model.base_url），无法探测。")
        return 2
    print(f"目标：{base_url}\n模型：{cfg.get('model')}  "
          f"| 会话 {args.sessions} 路 × 每路 {args.rounds} 轮")

    # 新建连接计数：探针侧包装（不改产品代码），复用的直接证据
    new_conns = {"n": 0}
    count_lock = threading.Lock()
    orig_new_conn = agent_http._new_conn

    def counting_new_conn(scheme, host, port, timeout):
        with count_lock:
            new_conns["n"] += 1
        return orig_new_conn(scheme, host, port, timeout)

    agent_http._new_conn = counting_new_conn
    agent_http.close_idle()          # 从空池起步：新建连接数 = 真实握手次数

    results, errors = {}, {}

    def session(idx: int):
        client = agent_llm.LLMClient(
            base_url=base_url, api_key=cfg.get("api_key") or "",
            model=cfg.get("model") or "", protocol=cfg.get("protocol") or "chat")
        lat = []
        try:
            for _ in range(args.rounds):
                t = time.perf_counter()
                out = client.chat_stream([{"role": "user", "content": PROMPT}],
                                         max_tokens=16)
                lat.append((time.perf_counter() - t) * 1000.0)
                if not (out.get("text") or "").strip():
                    errors[idx] = "空响应（上游未回正文）"
        except Exception as e:
            errors[idx] = repr(e)
        results[idx] = lat

    threads = [threading.Thread(target=session, args=(i,), name=f"session-{i}")
               for i in range(args.sessions)]
    t0 = time.perf_counter()
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    wall = (time.perf_counter() - t0) * 1000.0

    total = sum(sum(v) for v in results.values())
    print(f"\n并发墙钟 {wall:.0f} ms；Σ延迟 {total:.0f} ms →",
          "未串行化" if wall < total * 0.75 else "疑似串行化（检查池锁/事件循环）")
    for idx in sorted(results):
        lat = results[idx]
        first = f"{lat[0]:.0f}" if lat else "—"
        rest = " / ".join(f"{x:.0f}" for x in lat[1:]) or "—"
        print(f"  会话 {idx}：第 1 轮 {first} ms；后续轮 {rest} ms")
    print(f"新建连接 {new_conns['n']} 条（{args.sessions} 路会话并发 → 期望 ≈ {args.sessions}，"
          f"明显多用即复用未生效）")
    if errors:
        print(f"失败请求：{errors}")

    agent_http._new_conn = orig_new_conn
    agent_http.close_idle()
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())