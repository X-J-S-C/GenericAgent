// GenericAgent UI Prototype — view switching & interactions

(function () {
  'use strict';

  // ─── view switching ───
  function activate(viewName, opts) {
    // Switch view panels
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    // Sidebar nav: only the one matching the view
    document.querySelectorAll('.nav-item').forEach(el => {
      el.classList.toggle('active', el.dataset.view === viewName);
    });
    // Sessions: only ONE can be active — the clicked one
    document.querySelectorAll('.session').forEach(el => el.classList.remove('active'));
    if (opts && opts.sessionId) {
      const s = document.querySelector(`.session[data-session="${opts.sessionId}"]`);
      if (s) s.classList.add('active');
    } else if (viewName === 'chat') {
      const first = document.querySelector('.session');
      if (first) first.classList.add('active');
    }
    const target = document.querySelector(`.view[data-view="${viewName}"]`);
    if (target) {
      target.classList.add('active');
      target.style.animation = 'none';
      void target.offsetWidth;
      target.style.animation = '';
    }
    document.querySelector('.main')?.scrollTo({ top: 0, behavior: 'smooth' });
  }

  // nav buttons & sessions
  document.querySelectorAll('[data-view]').forEach(el => {
    el.addEventListener('click', e => {
      e.preventDefault();
      const view = el.dataset.view;
      if (!view) return;
      if (el.classList.contains('session')) {
        activate('chat', { sessionId: el.dataset.session });
        return;
      }
      activate(view);
    });
  });

  // explicit "go to" overrides (task rows / action cards jump into chat)
  document.querySelectorAll('[data-goto]').forEach(el => {
    el.addEventListener('click', e => {
      e.preventDefault();
      const target = el.dataset.goto;
      if (target) activate(target);
    });
  });

  // ─── composer auto-grow ───
  const ta = document.querySelector('.composer textarea');
  if (ta) {
    ta.addEventListener('input', () => {
      ta.style.height = 'auto';
      ta.style.height = Math.min(ta.scrollHeight, 200) + 'px';
    });
    ta.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        // prototype: just clear & bounce
        ta.value = '';
        ta.style.height = 'auto';
        const btn = document.querySelector('.composer-tools .send');
        if (btn) {
          btn.style.transform = 'scale(0.92)';
          setTimeout(() => (btn.style.transform = ''), 120);
        }
      }
    });
  }

  // ─── simulate a running task — alternate the pulse to feel alive ───
  // (no real backend; we just keep state dots visible)
  document.querySelectorAll('.tool-card.running').forEach(card => {
    const start = Date.now();
    const duration = card.querySelector('.tool-duration');
    if (!duration) return;
    const tick = setInterval(() => {
      const sec = ((Date.now() - start) / 1000).toFixed(1);
      duration.firstChild && (duration.firstChild.textContent = `执行中 ${sec}s`);
    }, 100);
    // stop after 30s in prototype so it doesn't run forever
    setTimeout(() => clearInterval(tick), 30000);
  });

  // ─── settings tab switching ───
  document.querySelectorAll('.settings-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      const panelId = tab.dataset.settings;
      // Update active tab
      document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      // Show corresponding panel
      document.querySelectorAll('.settings-panel').forEach(p => p.classList.remove('active'));
      const targetPanel = document.querySelector(`.settings-panel[data-panel="${panelId}"]`);
      if (targetPanel) targetPanel.classList.add('active');
    });
  });

  // ─── pet selector ───
  document.querySelectorAll('.pet-option').forEach(option => {
    option.addEventListener('click', () => {
      document.querySelectorAll('.pet-option').forEach(o => o.classList.remove('selected'));
      option.classList.add('selected');
    });
  });

  // ─── expose for debug ───
  window.__proto = { activate };
})();
