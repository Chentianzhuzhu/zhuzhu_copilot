/* === Weekly Report JavaScript === */
document.addEventListener('DOMContentLoaded', function() {
  // Smooth scrolling for navigation links
  document.querySelectorAll('.nav a').forEach(link => {
    link.addEventListener('click', function(e) {
      const href = this.getAttribute('href');
      if (href.startsWith('#')) {
        e.preventDefault();
        const target = document.querySelector(href);
        if (target) {
          target.scrollIntoView({
            behavior: 'smooth',
            block: 'start'
          });
        }
      }
    });
  });

  // Add active class to nav links on scroll
  const sections = document.querySelectorAll('.section');
  const navLinks = document.querySelectorAll('.nav a');

  window.addEventListener('scroll', () => {
    let current = '';
    sections.forEach(section => {
      const sectionTop = section.offsetTop;
      if (scrollY >= sectionTop - 200) {
        current = section.getAttribute('id');
      }
    });

    navLinks.forEach(link => {
      link.classList.remove('active');
      if (link.getAttribute('href') === `#${current}`) {
        link.classList.add('active');
      }
    });
  });

  // Intersection Observer for animations
  const observerOptions = {
    threshold: 0.1,
    rootMargin: '0px 0px -50px 0px'
  };

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.style.opacity = '1';
        entry.target.style.transform = 'translateY(0)';
      }
    });
  }, observerOptions);

  document.querySelectorAll('.card, .timeline-item, .problem-card, .plan-item').forEach(el => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(20px)';
    el.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
    observer.observe(el);
  });

  // Print button functionality
  const printBtn = document.querySelector('.print-btn');
  if (printBtn) {
    printBtn.addEventListener('click', () => {
      window.print();
    });
  }

  // Download as Markdown
  const downloadBtn = document.querySelector('.download-md');
  if (downloadBtn) {
    downloadBtn.addEventListener('click', () => {
      const content = generateMarkdown();
      const blob = new Blob([content], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'weekly_report.md';
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  // Generate Markdown content
  function generateMarkdown() {
    const title = document.querySelector('.header h1')?.textContent || '周报';
    const date = document.querySelector('.header p')?.textContent || new Date().toLocaleDateString();
    
    let md = `# ${title}\n\n`;
    md += `**日期**: ${date}\n\n`;
    md += `---\n\n`;

    document.querySelectorAll('.section').forEach(section => {
      const sectionTitle = section.querySelector('.section-title');
      if (sectionTitle) {
        md += `## ${sectionTitle.textContent}\n\n`;
      }
      
      const cards = section.querySelectorAll('.card, .timeline-item, .problem-card, .plan-item');
      cards.forEach(card => {
        const text = card.textContent.replace(/\s+/g, ' ').trim();
        md += `${text}\n\n`;
      });
    });

    return md;
  }

  // Initialize tooltips
  const tooltips = document.querySelectorAll('[data-tooltip]');
  tooltips.forEach(el => {
    el.addEventListener('mouseenter', (e) => {
      const tooltip = document.createElement('div');
      tooltip.className = 'tooltip';
      tooltip.textContent = el.dataset.tooltip;
      tooltip.style.cssText = `
        position: absolute;
        background: var(--text-primary);
        color: white;
        padding: 0.5rem 1rem;
        border-radius: 4px;
        font-size: 0.85rem;
        z-index: 1000;
        pointer-events: none;
        white-space: nowrap;
      `;
      document.body.appendChild(tooltip);
      
      const rect = el.getBoundingClientRect();
      tooltip.style.top = (rect.top - tooltip.offsetHeight - 10) + 'px';
      tooltip.style.left = (rect.left + rect.width / 2 - tooltip.offsetWidth / 2) + 'px';
      
      el._tooltip = tooltip;
    });
    
    el.addEventListener('mouseleave', () => {
      if (el._tooltip) {
        el._tooltip.remove();
        el._tooltip = null;
      }
    });
  });

  // Counter animation for stats
  const counters = document.querySelectorAll('.stat-card .number');
  counters.forEach(counter => {
    const target = parseInt(counter.textContent);
    if (!isNaN(target) && target > 0) {
      animateCounter(counter, target);
    }
  });

  function animateCounter(element, target) {
    let current = 0;
    const increment = target / 50;
    const timer = setInterval(() => {
      current += increment;
      if (current >= target) {
        element.textContent = target;
        clearInterval(timer);
      } else {
        element.textContent = Math.floor(current);
      }
    }, 30);
  }

  console.log('Weekly Report initialized successfully!');
});
