# -*- coding: utf-8 -*-
import os
base = r'C:\Users\zhuzhu\Desktop\my first android app'
site = os.path.join(base, 'website')

js = '''// zhuzhu_Copilot Website Scripts
document.addEventListener('DOMContentLoaded', function() {
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('animate-in');
      }
    });
  }, { threshold: 0.1 });
  
  document.querySelectorAll('.feature-card, .stat-card, .step').forEach(el => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(30px)';
    el.style.transition = 'opacity 0.6s ease, transform 0.6s ease';
    observer.observe(el);
  });
  
  const style = document.createElement('style');
  style.textContent = '.animate-in { opacity: 1 !important; transform: translateY(0) !important; }';
  document.head.appendChild(style);
  
  document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function(e) {
      e.preventDefault();
      const target = document.querySelector(this.getAttribute('href'));
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    });
  });
  
  let lastScroll = 0;
  window.addEventListener('scroll', () => {
    const nav = document.querySelector('.nav');
    if (nav) {
      if (window.scrollY > 50) {
        nav.style.boxShadow = '0 4px 20px rgba(0,0,0,0.08)';
      } else {
        nav.style.boxShadow = 'none';
      }
    }
  });
});
'''
with open(os.path.join(site, 'assets', 'script.js'), 'w', encoding='utf-8') as f:
    f.write(js)
print('js ok')
