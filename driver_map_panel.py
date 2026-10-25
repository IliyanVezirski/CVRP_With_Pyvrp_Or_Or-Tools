"""Optional driver-friendly presentation; never alters routes or service state."""
from branca.element import MacroElement, Template


def customer_panel_visibility_js():
    return """
    function initialiseCustomerPanel(container) {
      const panel=container.querySelector('.route-client-panel,.multi-course-panel,.client-panel');
      if (!panel || panel.dataset.driverVisibilityReady) return;
      panel.dataset.driverVisibilityReady='1';
      panel.id='driver-customers-'+document.querySelectorAll('[data-driver-visibility-ready]').length;
      const toggle=document.createElement('button');toggle.type='button';toggle.className='driver-panel-toggle';
      toggle.textContent=container.querySelector('.multi-course-selector,.course-selector')?'Курсове / Клиенти':'Клиенти';
      toggle.setAttribute('aria-controls',panel.id);toggle.setAttribute('aria-expanded','false');
      const close=document.createElement('button');close.type='button';close.className='driver-panel-close';close.textContent='×';
      close.setAttribute('aria-label','Скрий клиентите');close.title='Скрий клиентите';
      panel.querySelector('.route-client-header,.multi-course-header,.client-panel-header').append(close);
      container.insertBefore(toggle,panel);panel.hidden=true;
      function setOpen(open) {
        panel.hidden=!open;toggle.hidden=open;toggle.setAttribute('aria-expanded',String(open));
        const focus=open?close:toggle;if(focus.focus)focus.focus();
      }
      toggle.addEventListener('click',()=>setOpen(true));
      close.addEventListener('click',()=>setOpen(false));
    }
    """


def driver_panel_css():
    return """
    .bizant-mobile-driver .driver-card {
      display:grid !important; grid-template-columns:30px minmax(0,1fr); gap:8px;
      padding:12px 9px !important; margin-top:6px; border-radius:10px; min-height:44px;
    }
    .bizant-mobile-driver .driver-card:focus-visible {outline:2px solid #2563eb; outline-offset:-2px;}
    .bizant-mobile-driver .driver-card .route-client-name,
    .bizant-mobile-driver .driver-card .multi-course-client-name,
    .bizant-mobile-driver .driver-card .course-client-name {font-size:14px; text-align:left;}
    .driver-compact-meta {font:12px/1.5 Arial,sans-serif; color:#334155; margin-top:4px;}
    .driver-card-open .driver-compact-meta {display:none;}
    .bizant-mobile-driver .driver-card:not(.driver-card-open) .route-client-actions,
    .bizant-mobile-driver .driver-card:not(.driver-card-open) .multi-course-client-actions,
    .bizant-mobile-driver .driver-card:not(.driver-card-open) .client-actions,
    .bizant-mobile-driver .driver-card:not(.driver-card-open) .route-client-meta,
    .bizant-mobile-driver .driver-card:not(.driver-card-open) .route-client-time,
    .bizant-mobile-driver .driver-card:not(.driver-card-open) .route-client-turnover,
    .bizant-mobile-driver .driver-card:not(.driver-card-open) .multi-course-client-secondary,
    .bizant-mobile-driver .driver-card:not(.driver-card-open) .multi-course-client-arrival,
    .bizant-mobile-driver .driver-card:not(.driver-card-open) .course-client-secondary,
    .bizant-mobile-driver .driver-card:not(.driver-card-open) .course-client-arrival,
    .bizant-mobile-driver .driver-card:not(.driver-card-open) .single-client-detail {display:none !important;}
    .bizant-mobile-driver .driver-card .route-client-actions,
    .bizant-mobile-driver .driver-card .multi-course-client-actions,
    .bizant-mobile-driver .driver-card .client-actions {
      grid-column:1/-1; display:flex; flex-direction:row; gap:8px; margin-top:6px;
    }
    .bizant-mobile-driver .driver-card a {min-height:44px; box-sizing:border-box; padding:10px !important; font-size:13px !important;}
    .bizant-mobile-driver .driver-card .route-client-nav,
    .bizant-mobile-driver .driver-card .multi-course-client-nav,
    .bizant-mobile-driver .driver-card .client-nav {flex:2; border-radius:8px;}
    .bizant-mobile-driver .driver-card .route-client-street,
    .bizant-mobile-driver .driver-card .multi-course-client-street,
    .bizant-mobile-driver .driver-card .client-street {
      flex:1; background:#eef2f7; color:#334155 !important; box-shadow:none; border-radius:8px;
    }
    .driver-google-routes {margin:8px 0; padding:8px; border:1px solid #dce5ef; border-radius:10px;}
    .driver-google-routes summary {cursor:pointer; min-height:36px; display:flex; align-items:center; font-weight:700;}
    .driver-google-routes summary::before {content:'▸'; margin-right:7px;}
    .driver-google-routes[open] summary::before {content:'▾';}
    .driver-google-part {padding:10px 0; border-top:1px solid #e7edf4; font-size:12px;}
    .driver-google-part a {display:flex; align-items:center; justify-content:center; min-height:44px;
      background:#1a73e8; color:white; border-radius:8px; text-decoration:none; margin-top:8px;}
    .driver-stats-toggle {
      border:1px solid #d9e2ee; border-radius:8px; background:#f5f8fc; color:#23456a;
      padding:8px 12px; min-height:44px; font:13px Arial,sans-serif; cursor:pointer;
    }
    .driver-stats-toggle {display:block; width:100%; margin:0 0 6px;}
    .driver-stats-compact .route-client-stat:nth-child(n+3),
    .driver-stats-compact .client-route-stat:nth-child(n+3) {display:none;}
    .bizant-mobile-driver .route-client-panel,
    .bizant-mobile-driver .multi-course-panel,
    .bizant-mobile-driver #route-filter .client-panel {
      box-sizing:border-box;
      overflow:auto; overscroll-behavior:contain;
      height:85vh !important; height:85dvh !important;
      max-height:85vh !important; max-height:85dvh !important;
    }
    @media(max-width:700px) {
      .bizant-mobile-driver .driver-panel-toggle {
        position:fixed; top:max(12px,env(safe-area-inset-top)); right:max(12px,env(safe-area-inset-right));
        bottom:auto; left:auto; z-index:1001;
      }
      .bizant-mobile-driver .route-map-legend, .bizant-mobile-driver #legend {display:none;}
      .bizant-mobile-driver .route-client-panel,
      .bizant-mobile-driver .multi-course-panel,
      .bizant-mobile-driver #route-filter .client-panel {
        position:fixed !important; left:8px !important; right:8px !important; bottom:8px !important; top:auto !important;
        width:auto !important; max-width:none !important; box-sizing:border-box;
        overflow:auto; overscroll-behavior:contain; border-radius:16px 16px 10px 10px; padding:10px;
        padding-bottom:max(10px,env(safe-area-inset-bottom));
      }
    }
    """


def driver_panel_js():
    return """
    function enableMobileDriverPanel() {
      if (document.body.classList.contains('bizant-mobile-driver')) return;
      document.body.classList.add('bizant-mobile-driver');
      const selector = '.route-client-card, .multi-course-client-card, .client-list .route-row, .course-client-list .course-client-row';
      function updateCards(card, open) {
        const siblings = Array.from(card.parentElement.querySelectorAll('.driver-card'));
        siblings.forEach(item => {
          const expanded = item === card && open;
          item.classList.toggle('driver-card-open', expanded);
          item.setAttribute('aria-expanded', String(expanded));
        });
      }
      function toggleCard(event) {
        if (event.target.closest('a,button')) return;
        const card = event.target.closest('.driver-card');
        if (!card) return;
        if (event.type === 'keydown' && !['Enter',' '].includes(event.key)) return;
        event.preventDefault(); event.stopImmediatePropagation();
        updateCards(card, !card.classList.contains('driver-card-open'));
      }
      document.addEventListener('click', toggleCard, true);
      document.addEventListener('keydown', toggleCard, true);
      function prepare() {
        document.querySelectorAll(selector).forEach(card => {
          if (card.classList.contains('driver-card')) return;
          card.classList.add('driver-card'); card.tabIndex = 0;
          card.setAttribute('role','button'); card.setAttribute('aria-expanded','false');
          const main = card.querySelector('.route-client-main, .multi-course-client-main, .course-client-main, .single-client-main');
          if (main) {
            const meta = document.createElement('div'); meta.className = 'driver-compact-meta';
            meta.textContent = [card.dataset.driverArrival ? 'План: ' + card.dataset.driverArrival : '', card.dataset.driverVolume || ''].filter(Boolean).join(' · ');
            main.appendChild(meta);
          }
          // Open only the first client in a newly created course/list.
          if (card.parentElement.querySelector('.driver-card') === card) updateCards(card, true);
        });
        document.querySelectorAll('.route-client-stats, .client-route-stats').forEach(stats => {
          if (stats.dataset.driverReady) return;
          stats.dataset.driverReady = '1'; stats.classList.add('driver-stats-compact');
          const button = document.createElement('button'); button.type = 'button';
          button.className = 'driver-stats-toggle'; button.textContent = 'Данни за маршрута';
          button.setAttribute('aria-expanded','false');
          button.addEventListener('click', () => {
            const compact = stats.classList.toggle('driver-stats-compact');
            button.setAttribute('aria-expanded',String(!compact));
            button.textContent = compact ? 'Данни за маршрута' : 'Скрий подробностите';
          });
          stats.insertAdjacentElement('afterend',button);
        });
      }
      let queued = false;
      new MutationObserver(() => {
        if (queued) return; queued = true;
        requestAnimationFrame(() => { queued = false; prepare(); });
      }).observe(document.body, {childList:true, subtree:true});
      prepare();
    }
    """


class MobileDriverPanel(MacroElement):
    _template = Template("""
      {% macro header(this, kwargs) %}<style>{{ this.css | safe }}</style>{% endmacro %}
      {% macro script(this, kwargs) %}
        {{ this.javascript | safe }}
        enableMobileDriverPanel();
      {% endmacro %}
    """)

    def __init__(self):
        super().__init__()
        self._name = "MobileDriverPanel"
        self.css = driver_panel_css()
        self.javascript = driver_panel_js()
