/* Parser-blocking, same-origin bootstrap: apply route classes before body paint. */
(() => {
  const root=document.documentElement;
  root.classList.toggle('fde-signin',location.pathname.startsWith('/signin'));
  root.classList.toggle('fde-account',location.pathname.startsWith('/account'));
})();
