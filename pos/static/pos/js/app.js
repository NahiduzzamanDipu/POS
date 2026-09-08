/* Shared interface behaviour: sidebar, dropdowns, toasts, confirm dialogs and
   busy-state on submit.

   Everything here is progressive: with JavaScript off the sidebar is still
   reachable, forms still submit, and messages still read. No framework, no
   CDN -- the till has to work when the shop's internet does not. */
(function () {
  'use strict';

  // ------------------------------------------------------------- sidebar
  var sidebar = document.getElementById('sidebar');
  var scrim = document.getElementById('scrim');
  var toggle = document.getElementById('sidebar-toggle');
  var closeBtn = document.getElementById('sidebar-close');

  function setSidebar(open) {
    if (!sidebar) { return; }
    sidebar.classList.toggle('is-open', open);
    if (scrim) { scrim.classList.toggle('is-open', open); }
    if (toggle) { toggle.setAttribute('aria-expanded', String(open)); }
    document.body.style.overflow = open ? 'hidden' : '';
  }

  if (toggle) { toggle.addEventListener('click', function () { setSidebar(true); }); }
  if (closeBtn) { closeBtn.addEventListener('click', function () { setSidebar(false); }); }
  if (scrim) { scrim.addEventListener('click', function () { setSidebar(false); }); }

  // ----------------------------------------------------------- dropdowns
  function closeDropdowns(except) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-dropdown]'), function (root) {
      if (root === except) { return; }
      var menu = root.querySelector('[data-dropdown-menu]');
      var button = root.querySelector('[data-dropdown-toggle]');
      if (menu) { menu.hidden = true; }
      if (button) { button.setAttribute('aria-expanded', 'false'); }
    });
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-dropdown]'), function (root) {
    var button = root.querySelector('[data-dropdown-toggle]');
    var menu = root.querySelector('[data-dropdown-menu]');
    if (!button || !menu) { return; }

    button.addEventListener('click', function (event) {
      event.stopPropagation();
      var opening = menu.hidden;
      closeDropdowns(root);
      menu.hidden = !opening;
      button.setAttribute('aria-expanded', String(opening));
    });
    menu.addEventListener('click', function (event) { event.stopPropagation(); });
  });

  document.addEventListener('click', function () { closeDropdowns(null); });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
      closeDropdowns(null);
      setSidebar(false);
      closeModal();
    }
  });

  // -------------------------------------------------------------- toasts
  function dismiss(toast) {
    toast.classList.add('is-leaving');
    window.setTimeout(function () {
      if (toast.parentNode) { toast.parentNode.removeChild(toast); }
    }, 200);
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-toast]'), function (toast) {
    var close = toast.querySelector('[data-toast-close]');
    if (close) { close.addEventListener('click', function () { dismiss(toast); }); }
    // Errors stay until dismissed; the rest clear themselves.
    if (!toast.classList.contains('toast--error')) {
      window.setTimeout(function () { dismiss(toast); }, 6000);
    }
  });

  // ------------------------------------------------------ confirm dialog
  // Any form or link carrying data-confirm gets a styled confirmation step
  // instead of the browser's window.confirm box.
  var modal = null;
  var pending = null;

  function closeModal() {
    if (modal) { modal.hidden = true; }
    pending = null;
  }

  function buildModal() {
    if (modal) { return modal; }
    modal = document.createElement('div');
    modal.className = 'modal';
    modal.hidden = true;
    modal.innerHTML =
      '<div class="modal__dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title">' +
        '<div class="modal__head">' +
          '<span class="modal__icon"><svg class="icon" aria-hidden="true"><use href="#i-alert-triangle"></use></svg></span>' +
          '<h2 id="confirm-title">Please confirm</h2>' +
        '</div>' +
        '<div class="modal__body" data-confirm-body></div>' +
        '<div class="modal__foot">' +
          '<button type="button" class="btn btn--ghost" data-confirm-cancel>Cancel</button>' +
          '<button type="button" class="btn btn--danger" data-confirm-ok>Confirm</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(modal);

    modal.addEventListener('click', function (event) {
      if (event.target === modal) { closeModal(); }
    });
    modal.querySelector('[data-confirm-cancel]').addEventListener('click', closeModal);
    modal.querySelector('[data-confirm-ok]').addEventListener('click', function () {
      var target = pending;
      closeModal();
      if (!target) { return; }
      if (target.tagName === 'FORM') {
        target.dataset.confirmed = 'yes';
        if (target.requestSubmit) { target.requestSubmit(target._confirmSubmitter || undefined); }
        else { target.submit(); }
      } else {
        window.location.href = target.href;
      }
    });
    return modal;
  }

  function ask(target, message, okLabel, tone) {
    var box = buildModal();
    box.querySelector('[data-confirm-body]').textContent = message;
    var ok = box.querySelector('[data-confirm-ok]');
    ok.textContent = okLabel || 'Confirm';
    ok.className = 'btn ' + (tone === 'safe' ? 'btn--success' : 'btn--danger');
    pending = target;
    box.hidden = false;
    ok.focus();
  }

  Array.prototype.forEach.call(document.querySelectorAll('form[data-confirm]'), function (form) {
    // Remember which button submitted, so name/value pairs survive the detour.
    Array.prototype.forEach.call(form.querySelectorAll('button[type="submit"]'), function (button) {
      button.addEventListener('click', function () { form._confirmSubmitter = button; });
    });
    form.addEventListener('submit', function (event) {
      if (form.dataset.confirmed === 'yes') { return; }
      event.preventDefault();
      ask(form, form.dataset.confirm, form.dataset.confirmLabel, form.dataset.confirmTone);
    });
  });

  Array.prototype.forEach.call(document.querySelectorAll('a[data-confirm]'), function (link) {
    link.addEventListener('click', function (event) {
      event.preventDefault();
      ask(link, link.dataset.confirm, link.dataset.confirmLabel, link.dataset.confirmTone);
    });
  });

  // ------------------------------------------------- submit busy-state
  // Stops a double click posting the same form twice, and shows that
  // something is happening on a slow report.
  Array.prototype.forEach.call(document.querySelectorAll('form[data-busy]'), function (form) {
    form.addEventListener('submit', function () {
      if (form.dataset.confirm && form.dataset.confirmed !== 'yes') { return; }
      var button = form.querySelector('button[type="submit"]');
      if (!button || button.disabled) { return; }
      window.setTimeout(function () {
        button.disabled = true;
        button.classList.add('is-busy');
      }, 0);
    });
  });

  // ------------------------------------------- filter bars submit on change
  // A select is a decision; making the user press Filter afterwards is a
  // needless second step.
  Array.prototype.forEach.call(document.querySelectorAll('form[data-autofilter] select'), function (select) {
    select.addEventListener('change', function () { select.form.submit(); });
  });
}());
