/* {name} — data layer and UI wiring.

   The store talks to the API when there is one and to localStorage when there
   is not. That is not a fallback for its own sake: the dashboard preview
   serves these files statically, so /api/* returns a 404 there, and without
   this the interface would look broken in the one place it is most often
   looked at. Run `python server.py` and the same code talks to SQLite.

   No framework. The list is small; rebuilding it is cheaper than diffing it. */

(function () {
  'use strict';

  var KEY = 'mvp.items';
  var live = null;                 // null until the first request settles

  /* ---- store ---- */

  async function apiAlive() {
    if (live !== null) return live;
    try {
      var res = await fetch('/api/items', { method: 'GET' });
      live = res.ok;
    } catch (e) {
      live = false;
    }
    document.body.dataset.mode = live ? 'api' : 'local';
    var note = document.getElementById('mode');
    if (note) {
      note.textContent = live
        ? 'connected to the API'
        : 'offline — stored in this browser only';
    }
    return live;
  }

  function localAll() {
    try { return JSON.parse(localStorage.getItem(KEY) || '[]'); }
    catch (e) { return []; }
  }
  function localSave(rows) { localStorage.setItem(KEY, JSON.stringify(rows)); }

  var store = {
    async list() {
      if (await apiAlive()) {
        var d = await (await fetch('/api/items')).json();
        return d.items || [];
      }
      return localAll().sort(function (a, b) { return a.done - b.done || b.id - a.id; });
    },

    async add(title) {
      title = (title || '').trim();
      if (!title) throw new Error('An item needs a title.');
      if (await apiAlive()) {
        var res = await fetch('/api/items', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ title: title })
        });
        var d = await res.json();
        if (!d.ok) throw new Error(d.error || 'Could not add it.');
        return d.item;
      }
      var rows = localAll();
      var item = { id: Date.now(), title: title, done: 0,
                   created: new Date().toISOString() };
      rows.push(item);
      localSave(rows);
      return item;
    },

    async setDone(id, done) {
      if (await apiAlive()) {
        await fetch('/api/items/' + id, {
          method: 'PATCH',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ done: done })
        });
        return;
      }
      var rows = localAll();
      rows.forEach(function (r) { if (r.id === id) r.done = done ? 1 : 0; });
      localSave(rows);
    },

    async remove(id) {
      if (await apiAlive()) {
        await fetch('/api/items/' + id, { method: 'DELETE' });
        return;
      }
      localSave(localAll().filter(function (r) { return r.id !== id; }));
    }
  };

  /* ---- interface ---- */

  var list = document.getElementById('list');
  var form = document.getElementById('add');
  var input = document.getElementById('title');
  var empty = document.getElementById('empty');
  var errBox = document.getElementById('error');

  function fail(message) {
    if (!errBox) return;
    errBox.textContent = message;
    errBox.hidden = !message;
  }

  function row(item) {
    var li = document.createElement('li');
    li.className = 'card item' + (item.done ? ' is-done' : '');

    var box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !!item.done;
    box.setAttribute('aria-label', 'Mark "' + item.title + '" done');
    box.addEventListener('change', async function () {
      await store.setDone(item.id, box.checked);
      render();
    });

    var label = document.createElement('span');
    label.className = 'item-title';
    label.textContent = item.title;          // textContent, never innerHTML

    var del = document.createElement('button');
    del.type = 'button';
    del.className = 'btn btn-ghost item-x';
    del.textContent = 'Remove';
    del.addEventListener('click', async function () {
      await store.remove(item.id);
      render();
    });

    li.append(box, label, del);
    return li;
  }

  async function render() {
    try {
      var items = await store.list();
      list.innerHTML = '';
      items.forEach(function (i) { list.appendChild(row(i)); });
      if (empty) empty.hidden = items.length > 0;
      fail('');
    } catch (e) {
      fail(e.message || String(e));
    }
  }

  if (form) {
    form.addEventListener('submit', async function (e) {
      e.preventDefault();
      try {
        await store.add(input.value);
        input.value = '';
        await render();
        input.focus();
      } catch (err) {
        fail(err.message);
      }
    });
  }

  render();
})();
