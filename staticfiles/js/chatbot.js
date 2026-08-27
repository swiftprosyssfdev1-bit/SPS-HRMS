(function () {
  var root = document.getElementById('hr-chatbot-root');
  if (!root) return;

  var toggleBtn = document.getElementById('hr-chatbot-toggle');
  var closeBtn = document.getElementById('hr-chatbot-close');
  var panel = document.getElementById('hr-chatbot-panel');
  var form = document.getElementById('hr-chatbot-form');
  var input = document.getElementById('hr-chatbot-input');
  var messages = document.getElementById('hr-chatbot-messages');

  function open() {
    panel.classList.remove('hr-chatbot-hidden');
    input.focus();
  }

  function close() {
    panel.classList.add('hr-chatbot-hidden');
  }

  toggleBtn.addEventListener('click', function () {
    if (panel.classList.contains('hr-chatbot-hidden')) {
      open();
    } else {
      close();
    }
  });

  closeBtn.addEventListener('click', close);

  function escapeHtml(str) {
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  // Turns any http(s) or site-relative (/path/...) URL in the text into a
  // clickable link, while HTML-escaping everything else so we never inject
  // raw markup from the bot reply.
  function linkify(text) {
    var urlPattern = /(https?:\/\/[^\s]+|(?<![\w/])\/[a-zA-Z0-9][^\s]*)/g;
    var parts = [];
    var lastIndex = 0;
    var match;
    while ((match = urlPattern.exec(text)) !== null) {
      parts.push(escapeHtml(text.slice(lastIndex, match.index)));
      var url = match[0].replace(/[.,)]+$/, ''); // trim trailing punctuation
      var trailing = match[0].slice(url.length);
      parts.push('<a href="' + escapeHtml(url) + '" class="hr-chatbot-link">' + escapeHtml(url) + '</a>' + escapeHtml(trailing));
      lastIndex = match.index + match[0].length;
    }
    parts.push(escapeHtml(text.slice(lastIndex)));
    return parts.join('');
  }

  function addMessage(text, who) {
    var div = document.createElement('div');
    div.className = 'hr-chatbot-msg hr-chatbot-msg-' + who;
    if (who === 'bot') {
      div.innerHTML = linkify(text);
    } else {
      div.textContent = text;
    }
    messages.appendChild(div);
    messages.scrollTop = messages.scrollHeight;
    return div;
  }

  function getCsrfToken() {
    var input = form.querySelector('input[name=csrfmiddlewaretoken]');
    return input ? input.value : '';
  }

  // Renders the "Forward to HR" / "Denied" choice after an unanswered
  // question, and posts the employee's choice to /chatbot/resolve/.
  function addOptions(options, originalMessage) {
    var wrap = document.createElement('div');
    wrap.className = 'hr-chatbot-options';

    options.forEach(function (opt) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'hr-chatbot-option-btn';
      btn.textContent = opt.label;
      btn.addEventListener('click', function () {
        Array.prototype.forEach.call(wrap.querySelectorAll('button'), function (b) {
          b.disabled = true;
        });
        wrap.classList.add('hr-chatbot-options-resolved');

        fetch('/chatbot/resolve/', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
          },
          body: JSON.stringify({ action: opt.action, message: originalMessage })
        })
          .then(function (res) { return res.json(); })
          .then(function (data) {
            addMessage(data.reply || "Sorry, something went wrong.", 'bot');
          })
          .catch(function () {
            addMessage("Sorry, I couldn't reach the server. Please try again.", 'bot');
          });
      });
      wrap.appendChild(btn);
    });

    messages.appendChild(wrap);
    messages.scrollTop = messages.scrollHeight;
  }

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;

    addMessage(text, 'user');
    input.value = '';

    var typing = addMessage('Typing...', 'bot');
    typing.classList.add('hr-chatbot-msg-typing');

    fetch('/chatbot/ask/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCsrfToken()
      },
      body: JSON.stringify({ message: text })
    })
      .then(function (res) { return res.json(); })
      .then(function (data) {
        typing.remove();
        addMessage(data.reply || "Sorry, something went wrong.", 'bot');
        if (data.options && data.options.length) {
          addOptions(data.options, data.message || text);
        }
      })
      .catch(function () {
        typing.remove();
        addMessage("Sorry, I couldn't reach the server. Please try again.", 'bot');
      });
  });
})();
