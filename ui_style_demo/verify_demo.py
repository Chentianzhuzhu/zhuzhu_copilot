# -*- coding: utf-8 -*-
"""用 Selenium 无头 Edge 对 ui_style_demo 做交互级验收。

验证（过程折叠版）：
1. 默认主题为 glass
2. 思考过程 >5 行自动折叠
3. AI 完成汇报后：过程区自动收起，只留正文；正文完整可见且不被收起
4. 点击「查看执行过程」展开过程，再点「收起执行过程」收起
5. 流式正文超过 5 行折叠、点击展开/收起
6. 五主题切换正常、SELF-CHECK PASS
"""
import time
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By

BASE = "http://localhost:8137/index.html"
WAIT_TYPING = 12


def main():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=900,1900")
    driver = webdriver.Edge(options=opts)
    try:
        driver.get(BASE)
        time.sleep(1)

        # 1. 默认主题 glass
        assert driver.execute_script("return document.body.dataset.theme") == "glass"
        assert driver.find_element(By.CSS_SELECTOR, ".switcher button.active").get_attribute("data-theme") == "glass"
        print("[PASS] 默认主题为毛玻璃 glass")

        # 2. 等待打字完成
        time.sleep(WAIT_TYPING)
        assert len(driver.find_elements(By.CSS_SELECTOR, ".ai-proc")) == 2
        assert len(driver.find_elements(By.CSS_SELECTOR, ".proc-btn")) == 2
        print("[PASS] 过程区与收起按钮已装配")

        # 3. 完成汇报后过程区自动收起
        turns = driver.find_elements(By.CSS_SELECTOR, ".ai-turn")
        for i, turn in enumerate(turns):
            assert "done" in turn.get_attribute("class"), f"第{i+1}个 AI 回合未收起过程区"
        print("[PASS] AI 完成汇报后过程区自动收起")

        # 4. 正文不被收起（stream 文本完整可见）
        streams = driver.find_elements(By.CSS_SELECTOR, ".stream")
        for s in streams:
            txt = s.find_element(By.CSS_SELECTOR, ".txt")
            assert txt.is_displayed(), "正文不可见"
            assert len(txt.text) > 0, "正文为空"
        print("[PASS] 正文完整可见、不被收起")

        # 4b. 思考过程 >5 行自动折叠
        bubbles = driver.find_elements(By.CSS_SELECTOR, ".think-bubble")
        folded_think = [b for b in bubbles if "folded" in b.get_attribute("class")]
        assert len(folded_think) == 1, f"应有 1 个超5行折叠的思考过程, 实际 {len(folded_think)}"
        print("[PASS] 思考过程 >5 行自动折叠")

        # 5. 过程区收起时不可见，点击按钮展开后可见
        b0 = turns[0]
        proc0 = b0.find_element(By.CSS_SELECTOR, ".ai-proc")
        assert proc0.size["height"] == 0, "过程区收起后仍有高度"
        btn = b0.find_element(By.CSS_SELECTOR, ".proc-btn")
        btn.click()
        time.sleep(0.9)
        assert "done" not in b0.get_attribute("class"), "点击后未展开过程区"
        assert proc0.size["height"] > 0, "展开后过程区仍无高度"
        assert "收起执行过程" in btn.text
        print("[PASS] 点击查看执行过程 -> 展开正常")

        btn.click()
        time.sleep(0.9)
        assert "done" in b0.get_attribute("class"), "再次点击未收起"
        assert proc0.size["height"] == 0, "收起后仍有高度"
        assert "查看执行过程" in btn.text
        print("[PASS] 点击收起执行过程 -> 收起正常")

        # 6. 正文永不折叠（无折叠按钮、无 folded 类、无遮罩）
        for s in streams:
            assert "folded" not in s.get_attribute("class"), "正文不应折叠"
            assert len(s.find_elements(By.CSS_SELECTOR, ".fold-btn")) == 0, "正文不应有折叠按钮"
        print("[PASS] 正文永不折叠")

        # 7. 主题切换
        for th in ["light", "paper", "dark", "glass", "terminal"]:
            driver.find_element(By.CSS_SELECTOR, f'button[data-theme="{th}"]').click()
            time.sleep(0.4)
            assert driver.execute_script("return document.body.dataset.theme") == th
        print("[PASS] 5 主题切换正常")

        # 8. 重播可完整重置
        driver.find_element(By.ID, "replay").click()
        time.sleep(1)
        assert len(driver.find_elements(By.CSS_SELECTOR, ".proc-btn")) == 0, "重播未重置 DOM"
        print("[PASS] 重播完整重置")

        # 9. SELF-CHECK
        hint = driver.find_element(By.ID, "hint").text
        assert "SELF-CHECK PASS" in hint, f"自检失败: {hint}"
        print(f"[PASS] {hint}")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
