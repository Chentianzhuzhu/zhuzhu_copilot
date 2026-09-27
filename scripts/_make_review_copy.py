# -*- coding: utf-8 -*-
"""生成"全部页展开"的审阅副本（仅供人工/截图逐页核对，交付文件仍为单页显示）。

做法：给所有 .deck 补 .on 让其可见，并把槽位可见性同步改为始终显示。
用法：python scripts/_make_review_copy.py <deck.html> <out.html>
"""
import sys

INJECT = """<script>
window.__syncSlots = function(){
  for (var m=0;m<__sl.length;m++){
    var s2 = __sl[m].__fitslot;
    if (s2){ s2.style.display = ''; }
    __sl[m].classList.add('on');
  }
};
window.addEventListener('load', function(){
  setTimeout(function(){ __syncSlots(); __fitSlots(); }, 300);
});
</script></body>"""


def main():
    src, dst = sys.argv[1], sys.argv[2]
    with open(src, encoding="utf-8") as f:
        h = f.read()
    h = h.replace("</body>", INJECT, 1)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(h)
    print(f"review copy: {dst} {len(h)} chars")


if __name__ == "__main__":
    main()
