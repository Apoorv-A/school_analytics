/** Read the CSRF cookie and return headers for state-changing fetch calls. */
function csrfHeaders(extra) {
  const headers = Object.assign({}, extra || {});
  const match = document.cookie.match(/(?:^|;\s*)sa_csrf=([^;]+)/);
  if (match) {
    headers["X-CSRF-Token"] = decodeURIComponent(match[1]);
  }
  return headers;
}
