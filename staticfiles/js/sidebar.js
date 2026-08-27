/* Turns each ".sidebar-section" header in the sidebar into a collapsible
 * submenu toggle for the ".sidebar-link" items that follow it, up to the
 * next ".sidebar-section" (or the end of the menu). Works purely on the
 * rendered DOM, so it needs no changes to base.html's per-role template
 * blocks — it just groups whatever section/link siblings Django output.
 *
 * State (open/collapsed) is remembered per section label in
 * localStorage, so it survives the full-page reloads this app does on
 * every navigation. Whichever section contains the current page's link
 * is always forced open on load, even if it was previously collapsed.
 */
document.addEventListener('DOMContentLoaded', function () {
  var menu = document.querySelector('.sidebar-menu');
  if (!menu) return;

  var children = Array.prototype.slice.call(menu.children);
  var sections = [];
  var activeGroup = null;

  children.forEach(function (el) {
    if (el.classList.contains('sidebar-section')) {
      var group = document.createElement('div');
      group.className = 'sidebar-group';
      el.parentNode.insertBefore(group, el.nextSibling);
      activeGroup = group;
      sections.push({ header: el, group: group });
    } else if (activeGroup && el.classList.contains('sidebar-link')) {
      activeGroup.appendChild(el);
    }
  });

  var currentPath = window.location.pathname;

  sections.forEach(function (section) {
    var label = section.header.textContent.trim();
    var storageKey = 'sidebarCollapsed:' + label;

    var hasActiveLink = Array.prototype.some.call(
      section.group.querySelectorAll('.sidebar-link'),
      function (a) { return a.getAttribute('href') === currentPath; }
    );

    var stored = localStorage.getItem(storageKey);
    var collapsed = stored === null ? false : stored === 'true';
    if (hasActiveLink) collapsed = false;

    var chevron = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    chevron.setAttribute('class', 'sidebar-chevron');
    chevron.setAttribute('width', '14');
    chevron.setAttribute('height', '14');
    chevron.setAttribute('viewBox', '0 0 24 24');
    chevron.setAttribute('fill', 'none');
    chevron.setAttribute('stroke', 'currentColor');
    chevron.setAttribute('stroke-width', '2');
    chevron.setAttribute('stroke-linecap', 'round');
    chevron.setAttribute('stroke-linejoin', 'round');
    var polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    polyline.setAttribute('points', '6 9 12 15 18 9');
    chevron.appendChild(polyline);
    section.header.appendChild(chevron);

    function applyState(isCollapsed) {
      section.header.classList.toggle('collapsed', isCollapsed);
      section.group.classList.toggle('collapsed', isCollapsed);
    }
    applyState(collapsed);

    section.header.addEventListener('click', function () {
      var nowCollapsed = !section.group.classList.contains('collapsed');
      applyState(nowCollapsed);
      localStorage.setItem(storageKey, String(nowCollapsed));
    });
  });
});
