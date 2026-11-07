/* ============================================================
   PREMIUM UI — Vanilla JS Controller
   Page loader logic + stagger index injection.
   ============================================================ */

(function () {
  'use strict';

  var loader = document.getElementById('hrmsPageLoader');

  /* ── Biometric Page Blacklist ────────────────────────────── */
  /* These pages host live camera streams. Showing the full-page
     loader overlay blocks the camera preview and freezes the UI. */
  var BIOMETRIC_PATHS = [
    '/attendance/enroll-face/',
    '/attendance/kiosk/',
    '/attendance/punch-in/'
  ];

  function isOnBiometricPage() {
    var p = window.location.pathname;
    return BIOMETRIC_PATHS.some(function (bp) { return p.indexOf(bp) !== -1; });
  }

  /* ── Helpers ─────────────────────────────────────────────── */

  function showLoader() {
    if (loader) {
      loader.classList.remove('hidden');
    }
  }

  function hideLoader() {
    if (loader) {
      loader.classList.add('hidden');
    }
  }

  /* Patterns that indicate a file-download / export URL */
  var EXPORT_RE = /export_csv|payslip_pdf|download|export_excel|export_pdf/i;

  /**
   * Decide whether a click on an <a> should trigger the loader.
   * Returns false for exports, downloads, modals, blank targets,
   * hash-only links, and javascript: URIs.
   */
  function shouldShowLoaderForLink(anchor) {
    /* Must be an anchor with an href */
    if (!anchor || !anchor.href) return false;

    var href = anchor.getAttribute('href') || '';

    /* Hash-only or javascript: */
    if (href === '#' || href === '' || href.startsWith('#') || href.startsWith('javascript:')) {
      return false;
    }

    /* target="_blank" — opens a new tab */
    if (anchor.target && anchor.target === '_blank') return false;

    /* download attribute */
    if (anchor.hasAttribute('download')) return false;

    /* Bootstrap component triggers (modal, collapse, dropdown, tab, etc.) */
    if (anchor.hasAttribute('data-bs-toggle')) return false;

    /* Export / file-download URL patterns */
    if (EXPORT_RE.test(href)) return false;

    /* External links (different origin) */
    try {
      var url = new URL(href, window.location.origin);
      if (url.origin !== window.location.origin) return false;
      /* Biometric / camera pages — never show loader when navigating TO them */
      if (BIOMETRIC_PATHS.some(function (bp) { return url.pathname.indexOf(bp) !== -1; })) return false;
    } catch (_) {
      return false;
    }

    /* Never show loader when already ON a biometric page */
    if (isOnBiometricPage()) return false;

    return true;
  }

  /**
   * Decide whether a form submission should trigger the loader.
   */
  function shouldShowLoaderForForm(form) {
    if (!form) return false;

    /* Forms that use fetch / AJAX mark themselves with data-no-loader
       to prevent the full-page overlay from trapping the screen. */
    if (form.hasAttribute('data-no-loader')) return false;

    var action = form.getAttribute('action') || '';

    /* Export endpoints */
    if (EXPORT_RE.test(action)) return false;

    /* Forms inside a modal — don't lock the screen */
    if (form.closest('.modal')) return false;

    return true;
  }

  /* ── Event Wiring ───────────────────────────────────────── */

  /* Hide loader when the page is fully ready.
     On biometric/camera pages, force-hide immediately so the
     camera stream is never obscured by the overlay. */
  document.addEventListener('DOMContentLoaded', function () {
    hideLoader();
    /* Extra safety: if we are on a camera page, ensure no residual
       active/visible class lingers from bfcache or race conditions. */
    if (isOnBiometricPage() && loader) {
      loader.classList.add('hidden');
      loader.style.display = 'none';
    }
  });

  /* Handle bfcache (back/forward) — pageshow fires even from cache */
  window.addEventListener('pageshow', function (e) {
    hideLoader();
    if (isOnBiometricPage() && loader) {
      loader.classList.add('hidden');
      loader.style.display = 'none';
    }
  });

  /* Intercept link clicks (delegated on document) */
  document.addEventListener('click', function (e) {
    var anchor = e.target.closest('a');
    if (anchor && shouldShowLoaderForLink(anchor)) {
      /* Small delay to let the browser start navigation */
      showLoader();
    }
  });

  /* Intercept form submissions.
     Never trigger the global loader on biometric pages — those
     pages use AJAX and manage their own button spinners. */
  document.addEventListener('submit', function (e) {
    if (isOnBiometricPage()) return;
    var form = e.target;
    if (shouldShowLoaderForForm(form)) {
      showLoader();
    }
  });

  /* ── Stagger Index Injector ─────────────────────────────── */

  function injectStaggerIndexes() {
    var selectors = [
      'tbody tr',
      '.stat-card',
      '.accordion-item',
      '.hrms-content .card'
    ];

    selectors.forEach(function (sel) {
      var items = document.querySelectorAll(sel);
      items.forEach(function (el, i) {
        /* Cap the delay so very long lists don't wait forever */
        el.style.setProperty('--i', Math.min(i, 25));
      });
    });
  }

  /* Run stagger injection after DOM is ready */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', injectStaggerIndexes);
  } else {
    injectStaggerIndexes();
  }

})();
