// Autosave for the Pass table: each changed cell is staged as väntande at once
// (start and end as a pair).
// Nothing else happens here; without JavaScript every row posts as a whole.
(function () {
  'use strict';
  var table = document.querySelector('.pass-table');
  if (!table) return;

  var GENERIC = 'Det gick inte att spara. Försök igen.';
  var STALE = 'Något ändrades samtidigt. Ladda om sidan.';
  var queue = Promise.resolve();

  function pendingCopy(n) {
    return n + (n === 1 ? ' väntande ändring' : ' väntande ändringar') + ', inga mejl skickade';
  }

  function paintBar(n) {
    var bar = document.querySelector('[data-pending-bar]');
    if (n < 1) {
      if (bar) bar.remove();
      return;
    }
    if (!bar) {
      bar = document.createElement('div');
      bar.className = 'pending-bar';
      bar.setAttribute('data-pending-bar', '');
      var text = document.createElement('span');
      text.className = 'pending-text';
      text.setAttribute('data-pending-text', '');
      var actions = document.createElement('span');
      actions.className = 'pending-actions';
      var link = document.createElement('a');
      link.className = 'btn btn-primary';
      link.href = table.getAttribute('data-granska');
      link.setAttribute('data-save-first', '');
      link.textContent = 'Granska';
      actions.appendChild(link);
      bar.appendChild(text);
      bar.appendChild(actions);
      var anchor = document.querySelector('.agent-hint');
      anchor.parentNode.insertBefore(bar, anchor);
    }
    bar.querySelector('[data-pending-text]').textContent = pendingCopy(n);
  }

  function paintRow(tr, row) {
    tr.classList.toggle('pend', row.pending);
    var sign = tr.querySelector('[data-sign]');
    if (sign) sign.textContent = row.pending ? (tr.getAttribute('data-kind') === 'new' ? '+' : '~') : '';
    // Cells are named as the server names a staged field, so one list paints them all.
    tr.querySelectorAll('[data-field]').forEach(function (cell) {
      cell.classList.toggle('cell-ch', row.changed.indexOf(cell.getAttribute('data-field')) !== -1);
    });
  }

  // Refused cells, per row, so a later save elsewhere on the row doesn't hide one.
  var refused = new WeakMap();
  var refusals = 0;

  function settle(tr, group, message) {
    var messages = refused.get(tr) || {};
    refused.set(tr, messages);
    if (message) {
      messages[group.key] = message;
      refusals += 1;
    } else {
      delete messages[group.key];
    }
    group.inputs.forEach(function (input) {
      input.closest('.cell').classList.toggle('cell-err', Boolean(message));
    });
    var left = Object.keys(messages).map(function (key) { return messages[key]; });
    tr.querySelector('.row-alert').textContent = left.join(' ');
  }

  // Start and end are saved together, each checked against the other's new value.
  function saveGroup(input, tr) {
    if (input.name === 'start' || input.name === 'end') {
      return {
        key: 'time',
        inputs: [tr.querySelector('input[name="start"]'), tr.querySelector('input[name="end"]')]
      };
    }
    return { key: input.name, inputs: [input] };
  }

  function send(input) {
    var form = input.form;
    var tr = input.closest('tr');
    var group = saveGroup(input, tr);
    var data = new FormData();
    // The row's identity and the cells being saved: the row's other cells stay out of it.
    ['csrf_token', 'slot_ids', 'entry_id'].forEach(function (name) {
      form.querySelectorAll('input[name="' + name + '"]').forEach(function (hidden) {
        data.append(name, hidden.value);
      });
    });
    group.inputs.forEach(function (cell) { data.append(cell.name, cell.value); });
    return fetch(form.action, {
      method: 'POST',
      body: data,
      credentials: 'same-origin',
      keepalive: true,
      headers: { 'X-Autosave': '1', 'Accept': 'application/json' }
    }).then(function (response) {
      if (response.status === 200) {
        return response.json().then(function (body) {
          settle(tr, group, '');
          paintRow(tr, body.row);
          paintBar(body.pending);
        });
      }
      if (response.status === 400) {
        return response.json().then(function (body) {
          var errors = body.errors || {};
          var first = Object.keys(errors)[0];
          settle(tr, group, first ? errors[first] : GENERIC);
        });
      }
      settle(tr, group, response.status === 409 ? STALE : GENERIC);
    }).catch(function () {
      settle(tr, group, GENERIC);
    });
  }

  table.addEventListener('change', function (event) {
    var input = event.target;
    if (!input.form || !input.name || input.type === 'hidden') return;
    if (!input.closest('.pass-row')) return;
    // One request at a time: two quick edits on a row must stage in order.
    queue = queue.then(function () { return send(input); });
  });

  // Leaving the page must wait for the last cell. A save refused while
  // waiting keeps the page, so its message isn't lost with it.
  function afterSaves(go) {
    var before = refusals;
    queue.then(function () {
      if (refusals === before) go();
    });
  }

  document.addEventListener('click', function (event) {
    var link = event.target.closest('a[data-save-first]');
    if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey) return;
    event.preventDefault();
    afterSaves(function () { window.location.href = link.href; });
  });

  table.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form.hasAttribute('data-save-first')) return;
    event.preventDefault();
    afterSaves(function () { form.submit(); });
  });

  // Enter would press the row's first button, Ta bort. Leaving the cell is what saves.
  table.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && event.target.tagName === 'INPUT') {
      event.preventDefault();
      event.target.blur();
    }
  });
})();
