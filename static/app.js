/**
 * E-Smart Dashboard — UI interactions (frontend only)
 */
(function () {
  'use strict';

  const STORAGE_THEME = 'esmart-theme';

  /* ---------- Theme ---------- */
  function getPreferredTheme() {
    const saved = localStorage.getItem(STORAGE_THEME);
    if (saved === 'light' || saved === 'dark') return saved;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(STORAGE_THEME, theme);
    document.querySelectorAll('.theme-toggle-btn').forEach((btn) => {
      const icon = btn.querySelector('.theme-icon');
      if (icon) icon.textContent = theme === 'dark' ? '☀️' : '🌙';
      btn.setAttribute('aria-label', theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
    });
    window.dispatchEvent(new CustomEvent('esmart-theme-change', { detail: { theme } }));
  }

  function initTheme() {
    applyTheme(getPreferredTheme());
    document.querySelectorAll('.theme-toggle-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        applyTheme(next);
      });
    });
  }

  /* ---------- Mobile sidebar ---------- */
  function initSidebar() {
    const toggle = document.querySelector('.sidebar-toggle');
    const overlay = document.querySelector('.sidebar-overlay');
    const sidebar = document.querySelector('.sidebar');

    if (!toggle || !sidebar) return;

    const open = () => {
      sidebar.classList.add('is-open');
      overlay?.classList.add('is-visible');
      document.body.classList.add('sidebar-open');
    };

    const close = () => {
      sidebar.classList.remove('is-open');
      overlay?.classList.remove('is-visible');
      document.body.classList.remove('sidebar-open');
    };

    toggle.addEventListener('click', () => {
      sidebar.classList.contains('is-open') ? close() : open();
    });

    overlay?.addEventListener('click', close);

    sidebar.querySelectorAll('.nav-links a, .sidebar-nav a').forEach((link) => {
      link.addEventListener('click', () => {
        if (window.innerWidth < 1024) close();
      });
    });
  }

  /* ---------- Animated counters ---------- */
  function animateCounter(el) {
    const target = parseFloat(el.getAttribute('data-count')) || 0;
    const prefix = el.getAttribute('data-prefix') || '';
    const suffix = el.getAttribute('data-suffix') || '';
    const isFloat = String(target).includes('.') || el.hasAttribute('data-decimals');
    const decimals = parseInt(el.getAttribute('data-decimals') || '0', 10);
    const duration = 1200;
    const start = performance.now();

    function frame(now) {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      const value = target * eased;
      el.textContent =
        prefix +
        (isFloat ? value.toFixed(decimals) : Math.round(value).toLocaleString()) +
        suffix;
      if (t < 1) requestAnimationFrame(frame);
    }

    requestAnimationFrame(frame);
  }

  function initCounters() {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting && !entry.target.dataset.animated) {
            entry.target.dataset.animated = 'true';
            animateCounter(entry.target);
          }
        });
      },
      { threshold: 0.3 }
    );

    document.querySelectorAll('[data-count]').forEach((el) => observer.observe(el));
  }

  /* ---------- Scroll reveal ---------- */
  function initReveal() {
    const els = document.querySelectorAll('.reveal');
    if (!els.length) return;

    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('is-visible');
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.08, rootMargin: '0px 0px -40px 0px' }
    );

    els.forEach((el, i) => {
      el.style.setProperty('--reveal-delay', `${Math.min(i * 0.06, 0.4)}s`);
      observer.observe(el);
    });
  }

  /* ---------- Modals ---------- */
  function initModals() {
    document.querySelectorAll('.modal').forEach((modal) => {
      modal.addEventListener('click', (e) => {
        if (e.target === modal) {
          modal.classList.remove('is-open');
          modal.style.display = 'none';
        }
      });
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        document.querySelectorAll('.modal.is-open').forEach((m) => {
          m.classList.remove('is-open');
          m.style.display = 'none';
        });
      }
    });
  }

  window.openModal = function (id) {
    const modal = document.getElementById(id);
    if (!modal) return;
    modal.style.display = 'flex';
    requestAnimationFrame(() => modal.classList.add('is-open'));
  };

  window.closeModal = function (id) {
    const modal = document.getElementById(id);
    if (!modal) return;
    modal.classList.remove('is-open');
    setTimeout(() => {
      modal.style.display = 'none';
    }, 280);
  };

  /* ---------- Chart.js global defaults ---------- */
  window.initEsmartCharts = function () {
    if (typeof Chart === 'undefined') return;

    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    const grid = isDark ? 'rgba(148, 163, 184, 0.12)' : 'rgba(15, 23, 42, 0.08)';
    const text = isDark ? '#94a3b8' : '#64748b';

    Chart.defaults.color = text;
    Chart.defaults.borderColor = grid;
    Chart.defaults.font.family = "'Plus Jakarta Sans', system-ui, sans-serif";
    Chart.defaults.animation = { duration: 900, easing: 'easeOutQuart' };
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.padding = 16;
    Chart.defaults.plugins.tooltip.backgroundColor = isDark ? 'rgba(15, 23, 42, 0.95)' : 'rgba(255,255,255,0.98)';
    Chart.defaults.plugins.tooltip.titleColor = isDark ? '#f1f5f9' : '#0f172a';
    Chart.defaults.plugins.tooltip.bodyColor = isDark ? '#cbd5e1' : '#334155';
    Chart.defaults.plugins.tooltip.borderColor = isDark ? 'rgba(99, 102, 241, 0.4)' : 'rgba(99, 102, 241, 0.25)';
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.padding = 12;
    Chart.defaults.plugins.tooltip.cornerRadius = 10;
  };

  window.esmartGradient = function (ctx, c1, c2) {
    const g = ctx.createLinearGradient(0, 0, 0, 280);
    g.addColorStop(0, c1);
    g.addColorStop(1, c2);
    return g;
  };

  /* ---------- Init ---------- */
  function init() {
    initTheme();
    initSidebar();
    initCounters();
    initReveal();
    initModals();
    initEsmartCharts();

    document.body.classList.add('is-loaded');

    window.addEventListener('esmart-theme-change', () => {
      if (typeof Chart !== 'undefined') {
        initEsmartCharts();
        Chart.helpers.each(Chart.instances, (instance) => instance.update());
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
