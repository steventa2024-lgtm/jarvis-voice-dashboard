/* Shared behaviour for every page.

   Two jobs only: reveal things as they scroll into view, and mark the current
   page in the navigation. Everything else belongs to the page that needs it.

   No framework, no build step. This file is meant to be read and edited. */

(function () {
  'use strict';

  /* ---- reveal on scroll ----

     IntersectionObserver rather than a scroll handler: the browser does the
     work off the main thread, and a scroll listener that recalculates layout
     is the commonest reason a page feels heavy.

     Elements start hidden in CSS only when this script is present, so a page
     with JS disabled still shows all of its content. */
  var reveals = document.querySelectorAll('.on-scroll');

  if (reveals.length && 'IntersectionObserver' in window &&
      !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    var seen = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('in');
        seen.unobserve(entry.target);        // reveal once, never re-hide
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -8% 0px' });

    reveals.forEach(function (el) { seen.observe(el); });
  } else {
    reveals.forEach(function (el) { el.classList.add('in'); });
  }

  /* ---- the small-screen navigation ----

     The CSS only collapses a nav inside a header carrying data-nav, and that
     attribute is set here — so a page whose script fails to load shows every
     link rather than hiding them behind a button nothing can press.

     A <button> would bring keyboard support with it. Handling keys here too
     means a <div class="nav-toggle"> still works, because that is what gets
     written more often than not. */
  var toggle = document.querySelector('.nav-toggle');
  var header = toggle && toggle.closest('.site-header');

  if (toggle && header) {
    header.setAttribute('data-nav', 'closed');
    if (toggle.tagName !== 'BUTTON') {
      toggle.setAttribute('role', 'button');
      toggle.setAttribute('tabindex', '0');
    }

    var setOpen = function (open) {
      header.setAttribute('data-nav', open ? 'open' : 'closed');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    };
    setOpen(false);

    toggle.addEventListener('click', function () {
      setOpen(header.getAttribute('data-nav') !== 'open');
    });
    toggle.addEventListener('keydown', function (e) {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      e.preventDefault();                       // Space would scroll the page
      setOpen(header.getAttribute('data-nav') !== 'open');
    });

    // Following a link should not leave the menu covering the page you land on.
    header.querySelectorAll('.site-nav a').forEach(function (a) {
      a.addEventListener('click', function () { setOpen(false); });
    });
  }

  /* ---- current page ----
     Marked with aria-current so it is announced, not merely coloured. */
  var here = location.pathname.split('/').pop() || 'index.html';
  document.querySelectorAll('.site-nav a').forEach(function (a) {
    var target = a.getAttribute('href');
    if (target === here || (here === 'index.html' && target === './')) {
      a.setAttribute('aria-current', 'page');
    }
  });
})();
