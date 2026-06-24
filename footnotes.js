/* ================================================================
   footnotes.js — 위키백과 스타일 각주: 호버 미리보기 + 클릭 이동
   의존: .footnotes 블록 안에 <p><sup>N</sup> ...본문...</p> 구조
================================================================ */
(function () {
  const fnList = document.querySelector('.footnotes');
  if (!fnList) return;

  let justNavigated = false;

  const fnMap = {};
  const fnParas = [];
  fnList.querySelectorAll('p').forEach(p => {
    const supEl = p.querySelector('sup');
    if (!supEl) return;
    const n = supEl.textContent.trim();
    p.id = 'fn' + n;

    const clone = p.cloneNode(true);
    clone.querySelector('sup').remove();
    fnMap[n] = clone.innerHTML.trim();
    fnParas.push({ p, n });
  });

  const tooltip = document.createElement('div');
  tooltip.className = 'fn-tooltip';
  document.body.appendChild(tooltip);

  function showTooltip(a, n) {
    tooltip.innerHTML = fnMap[n];
    tooltip.classList.add('show');
    const r = a.getBoundingClientRect();
    tooltip.style.top = (r.bottom + window.scrollY + 6) + 'px';
    tooltip.style.left = (r.left + window.scrollX) + 'px';
    requestAnimationFrame(() => {
      const tw = tooltip.offsetWidth;
      const maxLeft = window.scrollX + document.documentElement.clientWidth - tw - 16;
      if (parseFloat(tooltip.style.left) > maxLeft) tooltip.style.left = maxLeft + 'px';
    });
  }
  function hideTooltip() { tooltip.classList.remove('show'); }

  // 같은 각주 번호가 본문에 여러 번 인용될 수 있으므로, id 중복을 피하기 위해
  // 되돌아갈 대표 위치(↩가 가리킬 곳)는 첫 인용에만 부여한다.
  const fnRefs = {};
  document.querySelectorAll('article sup').forEach(sup => {
    if (sup.closest('.footnotes')) return;
    const n = sup.textContent.trim();
    if (!fnMap[n]) return;

    const a = document.createElement('a');
    a.href = '#fn' + n;
    if (!fnRefs[n]) a.id = 'fnref' + n;
    a.className = 'fn-ref';
    a.textContent = n;
    sup.replaceWith(a);
    if (!fnRefs[n]) fnRefs[n] = a;

    a.addEventListener('mouseenter', () => showTooltip(a, n));
    a.addEventListener('focus', () => { if (!justNavigated) showTooltip(a, n); });
    a.addEventListener('mouseleave', hideTooltip);
    a.addEventListener('blur', hideTooltip);
    a.addEventListener('click', hideTooltip);
  });

  // 본문에 인용되지 않은 각주(참고문헌 등)는 되돌아갈 곳이 없으므로 ↩ 생략
  fnParas.forEach(({ p, n }) => {
    if (!fnRefs[n]) return;
    const back = document.createElement('a');
    back.href = '#fnref' + n;
    back.className = 'fn-back';
    back.textContent = '↩';
    back.addEventListener('click', () => {
      justNavigated = true;
      hideTooltip();
      setTimeout(() => { justNavigated = false; }, 200);
    });
    p.appendChild(document.createTextNode(' '));
    p.appendChild(back);
  });

  function flashTarget() {
    const id = location.hash.slice(1);
    if (!id) return;
    const el = document.getElementById(id);
    if (!el) return;
    hideTooltip();
    el.classList.add('fn-flash');
    setTimeout(() => el.classList.remove('fn-flash'), 1500);
  }
  window.addEventListener('hashchange', flashTarget);
  flashTarget();
})();
