// Autosave for the Pass table: each changed cell is staged as väntande at once.
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

  function say(tr, message) {
    tr.querySelector('.row-alert').textContent = message;
  }

  function send(input) {
    var form = input.form;
    var tr = input.closest('tr');
    var data = new FormData();
    // The row's identity, never its other cells: only the changed field is staged.
    ['csrf_token', 'slot_ids', 'entry_id'].forEach(function (name) {
      form.querySelectorAll('input[name="' + name + '"]').forEach(function (hidden) {
        data.append(name, hidden.value);
      });
    });
    data.append(input.name, input.value);
    return fetch(form.action, {
      method: 'POST',
      body: data,
      credentials: 'same-origin',
      headers: { 'X-Autosave': '1', 'Accept': 'application/json' }
    }).then(function (response) {
      if (response.status === 200) {
        return response.json().then(function (body) {
          say(tr, '');
          paintRow(tr, body.row);
          paintBar(body.pending);
        });
      }
      if (response.status === 400) {
        return response.json().then(function (body) {
          var errors = body.errors || {};
          var first = Object.keys(errors)[0];
          say(tr, first ? errors[first] : GENERIC);
        });
      }
      say(tr, response.status === 409 ? STALE : GENERIC);
    }).catch(function () {
      say(tr, GENERIC);
    });
  }

  table.addEventListener('change', function (event) {
    var input = event.target;
    if (!input.form || !input.name || input.type === 'hidden') return;
    if (!input.closest('.pass-row')) return;
    // One request at a time: two quick edits on a row must stage in order.
    queue = queue.then(function () { return send(input); });
  });

  // Enter would press the row's first button, Ta bort. Leaving the cell is what saves.
  table.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && event.target.tagName === 'INPUT') {
      event.preventDefault();
      event.target.blur();
    }
  });
})();
