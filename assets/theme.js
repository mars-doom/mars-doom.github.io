// Light/dark toggle in the top bar. With no saved choice the site follows the
// device setting; a click saves the opposite of whatever is showing now.
(function () {
  var root = document.documentElement;
  var button = document.querySelector('.theme-toggle');
  var media = matchMedia('(prefers-color-scheme: dark)');

  window.doomTheme = function () {
    return root.dataset.theme || (media.matches ? 'dark' : 'light');
  };

  function label() {
    if (button) button.setAttribute('aria-label',
      window.doomTheme() === 'dark' ? 'Switch to light mode' : 'Switch to dark mode');
  }

  if (button) button.addEventListener('click', function () {
    var next = window.doomTheme() === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    try { localStorage.setItem('doom-theme', next); } catch (e) {}
    label();
    document.dispatchEvent(new Event('themechange'));
  });

  // Follow device changes while no choice is saved.
  media.addEventListener('change', function () {
    if (!root.dataset.theme) { label(); document.dispatchEvent(new Event('themechange')); }
  });

  label();
})();
