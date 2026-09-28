/**
 * UI Demo 应用主逻辑
 * 包含：视图路由、Dashboard图表、Kanban拖拽、Portfolio滚动动效、组件交互
 */

// ========== 全局状态 ==========
const AppState = {
  currentView: 'dashboard',
  theme: 'dark',
  pagination: {
    page: 1,
    perPage: 5
  },
  kanbanData: {
    todo: [
      { id: 1, title: '设计系统文档', desc: '完善组件库文档说明', tag: '设计', author: 'AZ' },
      { id: 2, title: '代码审查', desc: '审查PR #234', tag: '开发', author: 'BW' },
      { id: 3, title: '用户测试', desc: '安排第二轮可用性测试', tag: '测试', author: 'CX' }
    ],
    inProgress: [
      { id: 4, title: 'API集成', desc: '对接支付接口', tag: '后端', author: 'DY' },
      { id: 5, title: '性能优化', desc: '优化首屏加载', tag: '前端', author: 'EZ' }
    ],
    done: [
      { id: 6, title: '需求评审', desc: '完成Q4需求评审', tag: '产品', author: 'FA' },
      { id: 7, title: 'Bug修复', desc: '修复登录问题', tag: '修复', author: 'GB' }
    ]
  }
};

// ========== 错误追踪 ==========
window.__errors = [];
window.addEventListener('error', (e) => {
  window.__errors.push({
    type: 'error',
    message: e.message,
    file: e.filename,
    line: e.lineno,
    time: new Date().toISOString()
  });
  console.error('[UI Demo]', e.message);
});

window.addEventListener('unhandledrejection', (e) => {
  window.__errors.push({
    type: 'promise',
    message: e.reason?.message || 'Unhandled rejection',
    time: new Date().toISOString()
  });
});

// ========== 视图路由 ==========
function navigateTo(view) {
  // 隐藏所有视图
  document.querySelectorAll('.view-container').forEach(el => {
    el.classList.remove('active');
  });
  
  // 显示目标视图
  const target = document.getElementById(`view-${view}`);
  if (target) {
    target.classList.add('active');
    AppState.currentView = view;
    
    // 更新导航激活状态
    document.querySelectorAll('.nav-item').forEach(el => {
      el.classList.remove('active');
    });
    document.querySelector(`.nav-item[data-view="${view}"]`)?.classList.add('active');
    
    // 更新顶栏标题
    const titles = {
      dashboard: '数据仪表盘',
      admin: '后台管理',
      landing: '产品落地页',
      kanban: '任务看板',
      portfolio: '作品集',
      components: '组件库'
    };
    document.querySelector('.topbar-title').textContent = titles[view] || 'UI Demo';
    
    // 视图初始化
    if (view === 'dashboard') initDashboard();
    if (view === 'portfolio') initPortfolioScroll();
    
    console.log(`[UI Demo] 切换到视图: ${view}`);
  }
}

// ========== Dashboard 图表 ==========
function initDashboard() {
  drawLineChart();
  drawPieChart();
  animateStats();
}

function drawLineChart() {
  const canvas = document.getElementById('revenueChart');
  if (!canvas) return;
  
  const ctx = canvas.getContext('2d');
  const width = canvas.width = canvas.offsetWidth;
  const height = canvas.height = canvas.offsetHeight;
  
  // 清除画布
  ctx.clearRect(0, 0, width, height);
  
  // 数据
  const data = [65, 78, 52, 91, 84, 107, 95, 112, 98, 125, 118, 135];
  const labels = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月'];
  
  // 绘制网格
  ctx.strokeStyle = '#333';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = (height / 4) * i;
    ctx.beginPath();
    ctx.moveTo(40, y);
    ctx.lineTo(width - 20, y);
    ctx.stroke();
  }
  
  // 绘制折线
  const stepX = (width - 60) / (data.length - 1);
  const maxVal = Math.max(...data);
  
  ctx.strokeStyle = '#1e3a5f';
  ctx.lineWidth = 2;
  ctx.beginPath();
  
  data.forEach((val, i) => {
    const x = 50 + i * stepX;
    const y = height - 20 - (val / maxVal) * (height - 40);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  
  // 绘制点
  data.forEach((val, i) => {
    const x = 50 + i * stepX;
    const y = height - 20 - (val / maxVal) * (height - 40);
    
    ctx.fillStyle = '#1e3a5f';
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fill();
    
    // 标签
    if (i % 2 === 0) {
      ctx.fillStyle = '#666';
      ctx.font = '11px system-ui';
      ctx.textAlign = 'center';
      ctx.fillText(labels[i], x, height - 5);
    }
  });
}

function drawPieChart() {
  const canvas = document.getElementById('trafficChart');
  if (!canvas) return;
  
  const ctx = canvas.getContext('2d');
  const width = canvas.width = canvas.offsetWidth;
  const height = canvas.height = canvas.offsetHeight;
  
  ctx.clearRect(0, 0, width, height);
  
  const data = [35, 25, 20, 20];
  const colors = ['#1e3a5f', '#2d4a6f', '#3d5a7f', '#4d6a8f'];
  const labels = ['直接访问', '搜索引擎', '社交媒体', '推荐链接'];
  
  const total = data.reduce((a, b) => a + b, 0);
  let startAngle = -Math.PI / 2;
  
  data.forEach((val, i) => {
    const sliceAngle = (val / total) * Math.PI * 2;
    const endAngle = startAngle + sliceAngle;
    
    ctx.fillStyle = colors[i];
    ctx.beginPath();
    ctx.moveTo(width / 2, height / 2);
    ctx.arc(width / 2, height / 2, 80, startAngle, endAngle);
    ctx.closePath();
    ctx.fill();
    
    startAngle = endAngle;
  });
  
  // 中心圆（甜甜圈效果）
  ctx.fillStyle = '#1f1f1f';
  ctx.beginPath();
  ctx.arc(width / 2, height / 2, 50, 0, Math.PI * 2);
  ctx.fill();
  
  // 图例
  let legendY = 30;
  labels.forEach((label, i) => {
    ctx.fillStyle = colors[i];
    ctx.fillRect(width - 100, legendY, 12, 12);
    
    ctx.fillStyle = '#b0b0b0';
    ctx.font = '12px system-ui';
    ctx.textAlign = 'left';
    ctx.fillText(label, width - 80, legendY + 10);
    
    legendY += 20;
  });
}

function animateStats() {
  document.querySelectorAll('.stat-card-value').forEach(el => {
    const target = parseInt(el.textContent.replace(/[^0-9]/g, ''));
    if (isNaN(target)) return;
    
    let current = 0;
    const increment = target / 30;
    const timer = setInterval(() => {
      current += increment;
      if (current >= target) {
        current = target;
        clearInterval(timer);
      }
      el.textContent = Math.floor(current).toLocaleString();
    }, 20);
  });
}

// ========== 后台管理 ==========
function initAdmin() {
  renderTable();
}

const mockUsers = [
  { id: 1, name: '张三', email: 'zhangsan@example.com', role: '管理员', status: '活跃', date: '2024-01-15' },
  { id: 2, name: '李四', email: 'lisi@example.com', role: '编辑', status: '活跃', date: '2024-02-20' },
  { id: 3, name: '王五', email: 'wangwu@example.com', role: '用户', status: '禁用', date: '2024-03-10' },
  { id: 4, name: '赵六', email: 'zhaoliu@example.com', role: '编辑', status: '活跃', date: '2024-04-05' },
  { id: 5, name: '钱七', email: 'qianqi@example.com', role: '用户', status: '活跃', date: '2024-05-12' },
  { id: 6, name: '孙八', email: 'sunba@example.com', role: '管理员', status: '活跃', date: '2024-06-01' },
  { id: 7, name: '周九', email: 'zhoujiu@example.com', role: '用户', status: '禁用', date: '2024-07-18' },
  { id: 8, name: '吴十', email: 'wushi@example.com', role: '编辑', status: '活跃', date: '2024-08-22' }
];

function renderTable() {
  const tbody = document.getElementById('userTableBody');
  if (!tbody) return;
  
  const start = (AppState.pagination.page - 1) * AppState.pagination.perPage;
  const end = start + AppState.pagination.perPage;
  const pageData = mockUsers.slice(start, end);
  
  tbody.innerHTML = pageData.map(user => `
    <tr>
      <td>${user.id}</td>
      <td>${user.name}</td>
      <td>${user.email}</td>
      <td><span class="role-badge">${user.role}</span></td>
      <td><span class="status-badge ${user.status}">${user.status}</span></td>
      <td>${user.date}</td>
      <td>
        <button class="btn btn-secondary" style="padding: 6px 12px; font-size: 12px;" onclick="editUser(${user.id})">编辑</button>
      </td>
    </tr>
  `).join('');
  
  renderPagination();
}

function renderPagination() {
  const total = mockUsers.length;
  const pages = Math.ceil(total / AppState.pagination.perPage);
  const paginationEl = document.getElementById('pagination');
  
  if (!paginationEl) return;
  
  let html = '';
  for (let i = 1; i <= pages; i++) {
    html += `<button class="page-btn ${i === AppState.pagination.page ? 'active' : ''}" onclick="goToPage(${i})">${i}</button>`;
  }
  paginationEl.innerHTML = html;
}

function goToPage(page) {
  AppState.pagination.page = page;
  renderTable();
}

function editUser(id) {
  showToast('编辑用户功能待实现', 'info');
}

function toggleForm() {
  const form = document.getElementById('addUserForm');
  form.classList.toggle('active');
}

// ========== 落地页 ==========
function initLanding() {
  // 平滑滚动到锚点
  document.querySelectorAll('a[href^="#"]').forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      const target = document.querySelector(link.getAttribute('href'));
      if (target) {
        target.scrollIntoView({ behavior: 'smooth' });
      }
    });
  });
}

// ========== Kanban 拖拽 ==========
function initKanban() {
  const cards = document.querySelectorAll('.kanban-card');
  const columns = document.querySelectorAll('.kanban-column');
  
  cards.forEach(card => {
    card.addEventListener('dragstart', handleDragStart);
    card.addEventListener('dragend', handleDragEnd);
  });
  
  columns.forEach(column => {
    column.addEventListener('dragover', handleDragOver);
    column.addEventListener('dragenter', handleDragEnter);
    column.addEventListener('dragleave', handleDragLeave);
    column.addEventListener('drop', handleDrop);
  });
}

let draggedCard = null;

function handleDragStart(e) {
  draggedCard = e.target;
  e.target.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
}

function handleDragEnd(e) {
  e.target.classList.remove('dragging');
  draggedCard = null;
}

function handleDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
}

function handleDragEnter(e) {
  e.preventDefault();
  e.currentTarget.style.borderColor = '#1e3a5f';
}

function handleDragLeave(e) {
  e.currentTarget.style.borderColor = '';
}

function handleDrop(e) {
  e.preventDefault();
  const column = e.currentTarget;
  column.style.borderColor = '';
  
  if (draggedCard) {
    column.querySelector('.kanban-cards').appendChild(draggedCard);
    updateKanbanCounts();
  }
}

function updateKanbanCounts() {
  document.querySelectorAll('.kanban-column').forEach(col => {
    const count = col.querySelectorAll('.kanban-card').length;
    col.querySelector('.column-count').textContent = count;
  });
}

function addKanbanCard(columnId) {
  const titles = ['新任务', '待优化', '需审核', '进行中'];
  const descs = ['描述任务内容...', '优化用户体验...', '等待代码审查...', '正在开发中...'];
  const tags = ['开发', '设计', '产品', '测试'];
  const authors = ['AZ', 'BW', 'CX', 'DY'];
  
  const random = Math.floor(Math.random() * titles.length);
  const card = document.createElement('div');
  card.className = 'kanban-card';
  card.draggable = true;
  card.innerHTML = `
    <div class="card-title">${titles[random]}</div>
    <div class="card-desc">${descs[random]}</div>
    <div class="card-meta">
      <span class="card-tag">${tags[random]}</span>
      <span class="card-avatar">${authors[random]}</span>
    </div>
  `;
  
  card.addEventListener('dragstart', handleDragStart);
  card.addEventListener('dragend', handleDragEnd);
  
  document.getElementById(`col-${columnId}`).querySelector('.kanban-cards').appendChild(card);
  updateKanbanCounts();
}

// ========== Portfolio 滚动动效 ==========
function initPortfolioScroll() {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible');
      }
    });
  }, {
    threshold: 0.1,
    rootMargin: '0px 0px -50px 0px'
  });
  
  document.querySelectorAll('.portfolio-item').forEach(item => {
    observer.observe(item);
  });
}

// ========== 组件交互 ==========
function openModal(id) {
  const modal = document.getElementById(`modal-${id}`);
  if (modal) {
    modal.classList.add('active');
  }
}

function closeModal(id) {
  const modal = document.getElementById(`modal-${id}`);
  if (modal) {
    modal.classList.remove('active');
  }
}

function showToast(message, type = 'info') {
  const container = document.querySelector('.toast-container');
  if (!container) return;
  
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span class="toast-message">${message}</span>`;
  
  container.appendChild(toast);
  
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateX(20px)';
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}

function switchTab(tabId) {
  // 隐藏所有 tab 内容
  document.querySelectorAll('.tab-content').forEach(el => {
    el.classList.remove('active');
  });
  
  // 移除所有 tab 按钮激活状态
  document.querySelectorAll('.tab-btn').forEach(el => {
    el.classList.remove('active');
  });
  
  // 激活当前 tab
  const content = document.getElementById(`tab-${tabId}`);
  const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"`);
  
  if (content) content.classList.add('active');
  if (btn) btn.classList.add('active');
}

// ========== 主题切换 ==========
function toggleTheme() {
  const body = document.body;
  const current = body.getAttribute('data-theme');
  const next = current === 'light' ? 'dark' : 'light';
  
  body.setAttribute('data-theme', next);
  AppState.theme = next;
  
  // 更新按钮文字
  const btn = document.querySelector('.theme-toggle');
  if (btn) btn.textContent = next === 'dark' ? '浅色模式' : '深色模式';
  
  console.log(`[UI Demo] 切换到 ${next} 主题`);
}

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', () => {
  console.log('[UI Demo] 应用初始化完成');
  
  // 默认加载第一个视图
  navigateTo('dashboard');
  
  // 绑定导航点击
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
      const view = item.dataset.view;
      navigateTo(view);
    });
  });
  
  // 绑定主题切换
  const themeBtn = document.querySelector('.theme-toggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', toggleTheme);
  }
  
  // 绑定关闭模态框
  document.querySelectorAll('.modal-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) {
        overlay.classList.remove('active');
      }
    });
  });
  
  // 输出调试信息
  console.log('[UI Demo] 可用视图:', Object.keys(AppState).filter(k => k !== 'currentView' && k !== 'theme' && k !== 'pagination' && k !== 'kanbanData'));
});
