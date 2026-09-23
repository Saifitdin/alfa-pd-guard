"""Демо-страница для ручной проверки без Swagger и JSON.

Одна страница на голом HTML: текст → «Замаскировать» → маска и разбор по
сущностям → «Восстановить» → оригинал; отдельно — вся цепочка через LLM.
Никаких сборщиков и внешних ресурсов: страница отдаётся самим сервисом.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PD Guard — демо</title>
<style>
  :root { --dark:#14213D; --light:#FBFBF8; --acc:#E8C547; --blue:#2F6DB5; --muted:#4A5568; --line:#E3E6E4; }
  * { box-sizing:border-box }
  body { margin:0; background:var(--light); color:var(--dark); font:16px/1.5 -apple-system, "Segoe UI", Roboto, Arial, sans-serif }
  header { background:var(--dark); color:var(--light); padding:20px 24px }
  header h1 { margin:0; font-size:22px; font-weight:600 }
  header p { margin:6px 0 0; color:#BFD8D5; font-size:14px }
  main { max-width:1100px; margin:0 auto; padding:24px 16px 48px; display:grid; gap:20px }
  section { background:#fff; border:1px solid var(--line); border-radius:12px; padding:20px }
  h2 { margin:0 0 12px; font-size:18px }
  label { display:block; font-size:13px; color:var(--muted); margin:10px 0 6px }
  textarea, select, input { width:100%; font:inherit; padding:10px 12px; border:1px solid var(--line); border-radius:8px; background:#fff; color:inherit }
  textarea { min-height:96px; resize:vertical }
  .row { display:flex; gap:12px; flex-wrap:wrap; align-items:end }
  .row > * { flex:1 1 220px }
  button { font:inherit; font-weight:600; padding:11px 18px; border:0; border-radius:8px; cursor:pointer; background:var(--blue); color:#fff }
  button.alt { background:var(--dark) }
  button.gold { background:var(--acc); color:var(--dark) }
  button:disabled { opacity:.5; cursor:default }
  .out { margin-top:12px; padding:14px; background:#F4F6F5; border-radius:8px; white-space:pre-wrap; word-break:break-word; min-height:52px; font-family:ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size:14px }
  .out.ok { border-left:4px solid #2E8B57 }
  .out.err { border-left:4px solid #C8102E }
  table { width:100%; border-collapse:collapse; margin-top:12px; font-size:14px }
  th, td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line) }
  th { color:var(--muted); font-weight:600 }
  .hint { color:var(--muted); font-size:13px; margin-top:8px }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; background:#EEF3FA; color:var(--blue); font-size:12px; margin-right:4px }
  footer { text-align:center; color:var(--muted); font-size:13px; padding:12px }
  a { color:var(--blue) }
</style>
</head>
<body>
<header>
  <h1>PD Guard — модуль безопасности персональных данных</h1>
  <p>Ручная проверка без Swagger: маскирование, восстановление, цепочка через LLM.</p>
</header>
<main>
  <section>
    <h2>1. Маскирование и восстановление</h2>
    <div class="row">
      <div>
        <label for="system">Система-потребитель (стратегия маски)</label>
        <select id="system">
          <option value="loadtest">loadtest — частичная маска, как в контракте ТЗ</option>
          <option value="alfagen-chat">alfagen-chat — токены [FIO_1:F] для LLM</option>
          <option value="analytics-sandbox">analytics-sandbox — синтетика, без демаскирования</option>
        </select>
      </div>
      <div>
        <label for="pid">payload_id (ключ корреляции пары)</label>
        <input id="pid" value="demo-1">
      </div>
    </div>
    <label for="text">Текст с персональными данными</label>
    <textarea id="text">Клиент Петрова Анна Сергеевна, паспорт 4509 123456, карта 4276 3800 1234 5679, телефон +7 916 123-45-67. Поэт Александр Пушкин тут ни при чём.</textarea>
    <div class="row" style="margin-top:12px">
      <button id="btnMask">Замаскировать</button>
      <button id="btnDemask" class="alt" disabled>Восстановить из маски</button>
      <button id="btnTrap" class="gold">Подставить ловушку</button>
    </div>
    <label>Маска — то, что уйдёт в модель</label>
    <div id="masked" class="out"></div>
    <label>Восстановленный текст</label>
    <div id="restored" class="out"></div>
    <div id="report"></div>
    <p class="hint">Пара запросов в POST /process с одним payload_id: первый — маска, второй с той же маской — оригинал. Кнопка «Восстановить» делает ровно это.</p>
  </section>

  <section>
    <h2>2. Вся цепочка через LLM</h2>
    <label for="prompt">Запрос для модели</label>
    <textarea id="prompt">Клиент Петрова Анна Сергеевна, карта 4276 3800 1234 5679. Составь короткое вежливое сообщение о том, что карта перевыпущена, обратись по имени.</textarea>
    <div class="row" style="margin-top:12px">
      <button id="btnChat">Спросить модель через модуль</button>
    </div>
    <label>Ушло в модель</label>
    <div id="sent" class="out"></div>
    <label>Ответ модели (с плейсхолдерами)</label>
    <div id="raw" class="out"></div>
    <label>Ответ клиенту после демаскирования</label>
    <div id="answer" class="out"></div>
    <p class="hint" id="llmhint"></p>
  </section>
</main>
<footer>Swagger: <a href="/docs">/docs</a> · метрики: <a href="/stats">/stats</a> · код: <a href="https://github.com/Saifitdin/alfa-pd-guard">github.com/Saifitdin/alfa-pd-guard</a></footer>
<script>
(function () {
  const $ = (id) => document.getElementById(id);
  const sys = () => $('system').value;
  let lastMask = null;

  function show(el, text, cls) { el.textContent = text; el.className = 'out ' + (cls || ''); }
  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

  async function call(path, body) {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-System-Id': sys() }, body: JSON.stringify(body) });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((data && (data.error || JSON.stringify(data))) || ('HTTP ' + r.status));
    return data;
  }

  $('btnTrap').onclick = () => {
    $('text').value = 'Поэт Александр Пушкин родился в Москве. Отделение банка: г. Москва, ул. Каланчевская, д. 27. Номер заказа 1234 5678 1234 5670. Мой пин-код 4321, помогите вспомнить.';
  };

  $('btnMask').onclick = async () => {
    show($('masked'), '…'); show($('restored'), ''); $('report').innerHTML = ''; lastMask = null; $('btnDemask').disabled = true;
    try {
      const rep = await call('/v1/mask', { text: $('text').value, payload_id: $('pid').value });
      lastMask = rep.masked_text;
      show($('masked'), rep.masked_text, 'ok');
      $('btnDemask').disabled = false;
      if (!rep.detected.length) {
        $('report').innerHTML = '<p class="hint">Персональных данных не найдено — и это правильно, если текст был ловушкой.</p>';
        return;
      }
      let rows = rep.detected.map((e) => '<tr><td><span class="pill">' + esc(e.type) + '</span>' + esc(e.title) + '</td><td>' + esc(e.start) + '–' + esc(e.end) + '</td><td>' + esc(e.confidence) + '</td><td>' + esc(e.detector) + '</td></tr>').join('');
      $('report').innerHTML = '<table><tr><th>Тип</th><th>Позиция</th><th>Уверенность</th><th>Детектор</th></tr>' + rows + '</table><p class="hint">Стратегия: ' + esc(rep.strategy) + ' · обработка: ' + esc(rep.latency_ms) + ' мс</p>';
    } catch (e) { show($('masked'), 'Ошибка: ' + e.message, 'err'); }
  };

  $('btnDemask').onclick = async () => {
    show($('restored'), '…');
    try {
      const r = await call('/process', { payload: lastMask, payload_id: $('pid').value });
      const ok = r.result === $('text').value;
      show($('restored'), r.result + (ok ? '\\n\\n✓ совпадает с исходным текстом посимвольно' : ''), ok ? 'ok' : '');
    } catch (e) { show($('restored'), 'Ошибка: ' + e.message + (e.message.indexOf('demasking_disabled') >= 0 ? ' — этой системе демаскирование запрещено политикой' : ''), 'err'); }
  };

  $('btnChat').onclick = async () => {
    show($('sent'), '…'); show($('raw'), ''); show($('answer'), ''); $('llmhint').textContent = '';
    try {
      const r = await call('/v1/llm/chat?system=' + encodeURIComponent(sys()), { prompt: $('prompt').value });
      show($('sent'), r.prompt_sent_to_llm, 'ok');
      show($('raw'), r.llm_answer_raw);
      show($('answer'), r.answer, 'ok');
      const llm = r.llm || {};
      $('llmhint').textContent = llm.provider === 'mock'
        ? 'Модель недоступна отсюда (' + (llm.reason || '') + (llm.status ? ', HTTP ' + llm.status : '') + ') — сработала заглушка: плейсхолдеры целы, демаскирование прошло.'
        : 'Провайдер: ' + llm.provider + ' · модель: ' + llm.model + ' · ' + r.latency_ms + ' мс';
    } catch (e) { show($('sent'), 'Ошибка: ' + e.message, 'err'); }
  };
})();
</script>
</body>
</html>
"""


@router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
def demo_page() -> str:
    return _PAGE
