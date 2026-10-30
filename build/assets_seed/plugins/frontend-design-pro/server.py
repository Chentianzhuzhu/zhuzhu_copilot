"""MCP server（stdio）: frontend-design-pro

由 AI 设计工具实现 + 固定协议骨架组装，供 zhuzhu_Copilot 的 agent_mcp 客户端连接调用。
协议：newline-delimited JSON-RPC 2.0，stdin 读请求 / stdout 写响应（UTF-8）。
"""

import json
import sys


# ---------- 工具实现（真实 API/系统操作，AI 生成） ----------

def tool_generate_css_animation(args: dict):
        try:
            anim_type = args.get('animation_type', 'hover')
            elem_type = args.get('element_type', 'button')
            theme = args.get('style_theme', 'gradient')
        
            animations = {
                'hover': {
                    'button': '''/* Neon Hover Effect */
    .neon-button {
      padding: 15px 40px;
      background: transparent;
      border: 2px solid #00f3ff;
      color: #00f3ff;
      font-size: 18px;
      cursor: pointer;
      position: relative;
      overflow: hidden;
      transition: all 0.4s ease;
      box-shadow: 0 0 10px #00f3ff, inset 0 0 10px #00f3ff;
    }

    .neon-button:hover {
      background: #00f3ff;
      color: #0a0a0a;
      box-shadow: 0 0 20px #00f3ff, 0 0 40px #00f3ff, inset 0 0 20px #00f3ff;
      transform: scale(1.05);
    }

    .neon-button::before {
      content: '';
      position: absolute;
      top: 0;
      left: -100%;
      width: 100%;
      height: 100%;
      background: linear-gradient(90deg, transparent, rgba(255,255,255,0.4), transparent);
      transition: 0.5s;
    }

    .neon-button:hover::before {
      left: 100%;
    }''',
                    'card': '''/* Glassmorphism Card Hover */
    .glass-card {
      padding: 30px;
      background: rgba(255, 255, 255, 0.1);
      backdrop-filter: blur(10px);
      border-radius: 20px;
      border: 1px solid rgba(255, 255, 255, 0.2);
      transition: all 0.5s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    }

    .glass-card:hover {
      transform: translateY(-10px) rotateX(5deg);
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.3);
      background: rgba(255, 255, 255, 0.15);
    }'''
                },
                'entrance': {
                    'default': '''/* Fade Up Animation */
    @keyframes fadeUp {
      from {
        opacity: 0;
        transform: translateY(30px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    .animate-fade-up {
      animation: fadeUp 0.8s ease-out forwards;
    }

    /* Staggered Animation */
    .stagger-item {
      opacity: 0;
      animation: fadeUp 0.6s ease-out forwards;
    }

    .stagger-item:nth-child(1) { animation-delay: 0.1s; }
    .stagger-item:nth-child(2) { animation-delay: 0.2s; }
    .stagger-item:nth-child(3) { animation-delay: 0.3s; }
    .stagger-item:nth-child(4) { animation-delay: 0.4s; }
    .stagger-item:nth-child(5) { animation-delay: 0.5s; }'''
                },
                'loop': {
                    'default': '''/* Floating Animation */
    @keyframes float {
      0%, 100% { transform: translateY(0px); }
      50% { transform: translateY(-20px); }
    }

    .floating {
      animation: float 3s ease-in-out infinite;
    }

    /* Pulsing Glow */
    @keyframes pulseGlow {
      0%, 100% { 
        box-shadow: 0 0 20px rgba(0, 243, 255, 0.5);
      }
      50% { 
        box-shadow: 0 0 40px rgba(0, 243, 255, 0.8), 0 0 60px rgba(0, 243, 255, 0.4);
      }
    }

    .pulse-glow {
      animation: pulseGlow 2s ease-in-out infinite;
    }'''
                }
            }
        
            result = animations.get(anim_type, animations['hover'])
            selected = result.get(elem_type, result['default'])
        
            return f'''```css
{selected}
```

**使用说明：**
- 将CSS代码添加到你的样式表中
- 为元素添加对应的类名即可生效
- 可根据需要调整动画参数（时长、延迟、颜色等）
- 建议使用 prefers-reduced-motion 媒体查询照顾敏感用户'''
        except Exception as e:
            return f'生成动画代码时出错：{str(e)}'

def tool_generate_vue3_transition(args: dict):
    try:
        trans_type = args.get('transition_type', 'fade')
        comp_name = args.get('component_name', 'MyComponent')
        
        transitions = {
            'fade': f'''<template>\n  <transition name="fade" mode="out-in">\n    <component :is="currentComponent" :key="currentState" />\n  </transition>\n</template>\n\n<script setup>\nimport {{ ref, computed }} from 'vue'\n\nconst currentState = ref('home')\n\nconst currentComponent = computed(() => {{\n  const components = {{\n    home: () => import('./Home.vue'),\n    about: () => import('./About.vue')\n  }}
  return components[currentState.value] || components.home\n}})\n\nconst navigateTo = (state) => {{\n  currentState.value = state\n}}\n</script>\n\n<style scoped>\n/* Fade Transition */\n.fade-enter-active,\n.fade-leave-active {{\n  transition: opacity 0.4s ease;\n}}\n\n.fade-enter-from,\n.fade-leave-to {{\n  opacity: 0;\n}}\n</style>''',
            'slide': f'''<template>\n  <transition name="slide" mode="out-in">\n    <component :is="currentComponent" :key="currentState" />\n  </transition>\n</template>\n\n<script setup>\nimport {{ ref, computed }} from 'vue'\n\nconst currentState = ref('left')\n\nconst currentComponent = computed(() => {{
  return currentState.value === 'left' \n    ? () => import('./LeftPanel.vue')\n    : () => import('./RightPanel.vue')\n}})\n\nconst switchPanel = (direction) => {{\n  currentState.value = direction\n}}\n</script>\n\n<style scoped>\n/* Slide Transition */\n.slide-enter-active,\n.slide-leave-active {{\n  transition: transform 0.5s cubic-bezier(0.25, 0.8, 0.25, 1),\n              opacity 0.5s ease;\n}}\n\n.slide-enter-from {{\n  transform: translateX(100%);\n  opacity: 0;\n}}\n\n.slide-leave-to {{\n  transform: translateX(-100%);\n  opacity: 0;\n}}\n</style>''',
            'scale': f'''<template>\n  <transition name="scale" mode="out-in">\n    <component :is="currentComponent" :key="currentState" />\n  </transition>\n</template>\n\n<script setup>\nimport {{ ref, computed }} from 'vue'\n\nconst currentState = ref('modal')\n\nconst currentComponent = computed(() => {{
  return currentState.value === 'modal'\n    ? () => import('./Modal.vue')\n    : null\n}})\n\nconst toggleModal = () => {{\n  currentState.value = currentState.value === 'modal' ? '' : 'modal'\n}}\n</script>\n\n<style scoped>\n/* Scale Transition */\n.scale-enter-active,\n.scale-leave-active {{\n  transition: all 0.4s cubic-bezier(0.175, 0.885, 0.32, 1.275);\n}}\n\n.scale-enter-from,\n.scale-leave-to {{\n  transform: scale(0.5);\n  opacity: 0;\n}}\n</style>'''
        }
        
        result = transitions.get(trans_type, transitions['fade'])
        
        return f'''```vue\n{result}\n```\n\n**特性说明：**\n- mode="out-in" 确保退出动画完成后再进入\n- 使用动态组件实现高性能切换\n- 过渡时间可自定义调整\n- 支持Vue Router无缝集成'''
    except Exception as e:
        return f'生成Vue3过渡代码时出错：{str(e)}'

def tool_generate_scroll_animation(args: dict):
    try:
        effect = args.get('effect_type', 'reveal')
        targets = args.get('target_elements', 'section')
        
        effects = {
            'reveal': '''// Scroll Reveal Animation\nconst revealElements = document.querySelectorAll('.reveal');\n\nconst revealOnScroll = () => {{\n  revealElements.forEach(element => {{\n    const windowHeight = window.innerHeight;\n    const elementTop = element.getBoundingClientRect().top;\n    const revealPoint = 150;\n    
    if (elementTop < windowHeight - revealPoint) {{\n      element.classList.add('active');\n    }}\n  }});\n}};\n\n// Intersection Observer (Modern Approach)\nconst observerOptions = {{\n  threshold: 0.1,\n  rootMargin: "0px 0px -50px 0px"\n}};\n\nconst observer = new IntersectionObserver((entries) => {{\n  entries.forEach(entry => {{
    if (entry.isIntersecting) {{\n      entry.target.classList.add('active');\n      observer.unobserve(entry.target); // 只触发一次\n    }}\n  }});\n}}, observerOptions);\n\ndocument.querySelectorAll('.reveal').forEach(el => observer.observe(el));\n\n// CSS\n.reveal {{\n  opacity: 0;\n  transform: translateY(50px);\n  transition: all 0.8s ease-out;\n}}\n\n.reveal.active {{\n  opacity: 1;\n  transform: translateY(0);\n}}\n\n/* Stagger children */\n.reveal-item {{\n  opacity: 0;\n  transform: translateY(30px);\n  transition: all 0.6s ease;\n}}\n\n.reveal.active .reveal-item {{\n  opacity: 1;\n  transform: translateY(0);\n}}\n\n.reveal.active .reveal-item:nth-child(1) {{ transition-delay: 0.1s; }}\n.reveal.active .reveal-item:nth-child(2) {{ transition-delay: 0.2s; }}\n.reveal.active .reveal-item:nth-child(3) {{ transition-delay: 0.3s; }}\n.reveal.active .reveal-item:nth-child(4) {{ transition-delay: 0.4s; }}''',
            'parallax': '''// Parallax Scrolling Effect\nconst parallaxElements = document.querySelectorAll('.parallax');\n\nwindow.addEventListener('scroll', () => {{\n  const scrollY = window.scrollY;\n  \n  parallaxElements.forEach(el => {{\n    const speed = el.dataset.speed || 0.5;\n    el.style.transform = `translateY(${{scrollY * speed}}px)`;\n  }});\n}});\n\n// Alternative: CSS-only parallax\n.parallax-section {{\n  height: 100vh;\n  background-attachment: fixed;\n  background-position: center;\n  background-repeat: no-repeat;\n  background-size: cover;\n}}\n\n/* Multi-layer parallax */\n.parallax-layer-1 {{ transform: translateY(calc(scrollY * 0.2)); }}\n.parallax-layer-2 {{ transform: translateY(calc(scrollY * 0.4)); }}\n.parallax-layer-3 {{ transform: translateY(calc(scrollY * 0.6)); }}''',
            'marquee': '''// Infinite Marquee Animation\nconst marqueeContainer = document.querySelector('.marquee-container');\nconst marqueeContent = document.querySelector('.marquee-content');\n\n// Duplicate content for seamless loop\nmarqueeContent.innerHTML += marqueeContent.innerHTML;\n\nlet position = 0;\nfunction animateMarquee() {{\n  position -= 1;
  if (Math.abs(position) >= marqueeContent.offsetWidth / 2) {{\n    position = 0;\n  }}\n  marqueeContent.style.transform = `translateX(${{position}}px)`;\n  requestAnimationFrame(animateMarquee);\n}}\n\nanimateMarquee();\n\n// Pause on hover\nmarqueeContainer.addEventListener('mouseenter', () => {{\n  cancelAnimationFrame(requestAnimationFrame(animateMarquee));\n}});\n\nmarqueeContainer.addEventListener('mouseleave', () => {{\n  animateMarquee();\n}});\n\n/* CSS Alternative */\n.marquee {{\n  overflow: hidden;\n  white-space: nowrap;\n}}\n\n.marquee-content {{\n  display: inline-block;\n  animation: marquee 20s linear infinite;\n}}\n\n@keyframes marquee {{\n  0% {{ transform: translateX(0); }}\n  100% {{ transform: translateX(-50%); }}\n}}'''
        }
        
        result = effects.get(effect, effects['reveal'])
        
        return f'''```javascript\n{result}\n```\n\n**性能优化建议：**\n- 使用 requestAnimationFrame 替代 setInterval\n- 限制监听器数量，避免内存泄漏\n- 考虑使用 will-change 属性提示浏览器优化\n- 移动端禁用复杂滚动效果'''
    except Exception as e:
        return f'生成滚动动画代码时出错：{str(e)}'

def tool_generate_js_microinteractions(args: dict):
    try:
        interaction = args.get('interaction_type', 'click')
        selector = args.get('element_selector', '.btn')
        
        interactions = {
            'click': f'''// Enhanced Click Feedback\ndocument.querySelectorAll('{selector}').forEach(button => {{\n  button.addEventListener('click', function(e) {{\n    // Create ripple effect\n    const ripple = document.createElement('span');\n    ripple.classList.add('ripple');\n    \n    const rect = this.getBoundingClientRect();\n    const size = Math.max(rect.width, rect.height);\n    const x = e.clientX - rect.left - size / 2;\n    const y = e.clientY - rect.top - size / 2;\n    \n    ripple.style.width = ripple.style.height = size + 'px';\n    ripple.style.left = x + 'px';\n    ripple.style.top = y + 'px';\n    \n    this.appendChild(ripple);\n    \n    setTimeout(() => ripple.remove(), 600);\n    \n    // Button press effect\n    this.style.transform = 'scale(0.95)';\n    setTimeout(() => {{\n      this.style.transform = 'scale(1)';\n    }}, 150);\n  }});\n}});\n\n// CSS\n{selector} {{\n  position: relative;\n  overflow: hidden;\n  transition: transform 0.15s ease;\n}}\n\n.ripple {{\n  position: absolute;\n  border-radius: 50%;\n  background: rgba(255, 255, 255, 0.6);\n  transform: scale(0);\n  animation: ripple-animation 0.6s ease-out;\n  pointer-events: none;\n}}\n\n@keyframes ripple-animation {{\n  to {{\n    transform: scale(4);\n    opacity: 0;\n  }}\n}}''',
            'drag': f'''// Smooth Drag Interaction\nconst draggable = document.querySelector('{selector}');\nlet isDragging = false;\nlet currentX;\nlet currentY;\nlet initialX;\nlet initialY;\nlet xOffset = 0;\nlet yOffset = 0;\n\ndraggable.addEventListener('mousedown', dragStart);\ndocument.addEventListener('mouseup', dragEnd);\ndocument.addEventListener('mousemove', drag);\n\nfunction dragStart(e) {{\n  initialX = e.clientX - xOffset;\n  initialY = e.clientY - yOffset;\n  
  if (e.target === draggable) {{\n    isDragging = true;\n    draggable.style.cursor = 'grabbing';\n  }}\n}}\n\nfunction dragEnd(e) {{\n  initialX = currentX;\n  initialY = currentY;\n  isDragging = false;\n  draggable.style.cursor = 'grab';\n}}\n\nfunction drag(e) {{
  if (isDragging) {{\n    e.preventDefault();\n    currentX = e.clientX - initialX;\n    currentY = e.clientY - initialY;\n    \n    xOffset = currentX;\n    yOffset = currentY;\n    \n    setTranslate(currentX, currentY, draggable);\n  }}\n}}\n\nfunction setTranslate(xPos, yPos, el) {{\n  el.style.transform = `translate3d(${{xPos}}px, ${{yPos}}px, 0)`;\n  el.style.transition = 'transform 0.1s ease-out';\n}}''',
            'spring': f'''// Spring Physics Animation\nclass SpringAnimation {{\n  constructor(element) {{\n    this.element = element;\n    this.velocity = 0;\n    this.position = 0;\n    this.target = 0;\n    this.stiffness = 0.1;\n    this.damping = 0.85;\n    this.animationId = null;\n  }}\n  \n  start() {{\n    const animate = () => {{\n      const force = (this.target - this.position) * this.stiffness;\n      this.velocity += force;\n      this.velocity *= this.damping;\n      this.position += this.velocity;\n      \n      this.element.style.transform = `translateX(${{this.position}}px)`;\n      
      if (Math.abs(this.velocity) > 0.01 || Math.abs(this.target - this.position) > 0.1) {{\n        this.animationId = requestAnimationFrame(animate);\n      }}\n    }};\n    \n    this.animationId = requestAnimationFrame(animate);\n  }}\n  \n  stop() {{
    if (this.animationId) {{\n      cancelAnimationFrame(this.animationId);\n    }}\n  }}\n  \n  setTarget(value) {{\n    this.target = value;\n    this.start();\n  }}\n}}\n\n// Usage\nconst springEl = document.querySelector('{selector}');\nconst spring = new SpringAnimation(springEl);\n\nspringEl.addEventListener('mouseenter', () => spring.setTarget(20));\nspringEl.addEventListener('mouseleave', () => spring.setTarget(0));'''
        }
        
        result = interactions.get(interaction, interactions['click'])
        
        return f'''```javascript\n{result}\n```\n\n**最佳实践：**\n- 使用 transform 而非 top/left 提升性能\n- 添加 transitionend 事件监听清理状态\n- 移动端优化触摸事件处理\n- 考虑 prefers-reduced-motion 无障碍支持'''
    except Exception as e:
        return f'生成微交互代码时出错：{str(e)}'

# ---------- 工具注册表 ----------

TOOLS = [
    {"name": "generate_css_animation", "description": "生成炫酷的CSS动画代码，支持悬停、入场、循环等效果", "inputSchema": {"type": "object", "properties": {"animation_type": {"type": "string", "description": "动画类型：hover/entrance/loop/pulse/flip/zoom"}, "element_type": {"type": "string", "description": "元素类型：button/card/icon/image/text"}, "style_theme": {"type": "string", "description": "视觉主题：neon/glassmorphism/gradient/minimal/dark"}}, "required": ["animation_type", "element_type"]}},
    {"name": "generate_vue3_transition", "description": "生成Vue3组件过渡动画代码，支持单元素和列表动画", "inputSchema": {"type": "object", "properties": {"transition_type": {"type": "string", "description": "过渡类型：fade/slide/scale/flip/list"}, "component_name": {"type": "string", "description": "组件名称，如：MyComponent"}}, "required": ["transition_type", "component_name"]}},
    {"name": "generate_scroll_animation", "description": "生成基于滚动的动画效果代码，支持Intersection Observer API", "inputSchema": {"type": "object", "properties": {"effect_type": {"type": "string", "description": "滚动效果类型：reveal/fade-in/slide-up/parallax/marquee"}, "target_elements": {"type": "string", "description": "目标元素描述：section/card/text/image"}}, "required": ["effect_type"]}},
    {"name": "generate_js_microinteractions", "description": "生成JavaScript微交互动画，包括点击、拖拽、悬停等交互效果", "inputSchema": {"type": "object", "properties": {"interaction_type": {"type": "string", "description": "交互类型：click/drag/ripple/spring/bounce"}, "element_selector": {"type": "string", "description": "目标元素选择器，如：.btn, #submit"}}, "required": ["interaction_type", "element_selector"]}}
]

_HANDLERS = {
     "generate_css_animation": tool_generate_css_animation,
    "generate_vue3_transition": tool_generate_vue3_transition,
    "generate_scroll_animation": tool_generate_scroll_animation,
    "generate_js_microinteractions": tool_generate_js_microinteractions,
 }


# ---------- JSON-RPC 分发 ----------

def _handle(msg: dict):
    """处理单个请求/通知；通知（无 id）返回 None 不回复"""
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}
    rid = msg.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "frontend-design-pro", "version": "1.0"},
        }}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        tname = params.get("name", "")
        fn = _HANDLERS.get(tname)
        if fn is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"未知工具: {tname}"}}
        try:
            text = fn(params.get("arguments", {}) or {})
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except Exception as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
        return {"jsonrpc": "2.0", "id": rid, "result": result}
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": f"未知方法: {method}"}}


def main():
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        resp = _handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp, ensure_ascii=False).encode("utf-8") + b"\n")
            stdout.flush()


if __name__ == "__main__":
    main()
