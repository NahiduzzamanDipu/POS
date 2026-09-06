/* POS terminal: search, cart, customer lookup and totals.

   Money is never decided here. The browser previews nothing it invents: the
   server returns the authoritative quote for the cart, and decides customer
   eligibility again when the sale is submitted. This file only moves the
   cashier through search -> cart -> payment as quickly as possible. */
(function () {
  'use strict';

  var config = window.POS_CONFIG || {};
  var cart = new Map();          // productId -> {id, name, price, discount, final, stock, qty}
  var results = [];              // what is currently on screen, for keyboard use
  var cursor = -1;               // highlighted result index
  var paymentMethod = 'CASH';
  var quoteTimer = null;
  var searchTimer = null;
  var customerTimer = null;
  var currentTotal = 0;

  var els = {
    search: document.getElementById('product-search'),
    resultList: document.getElementById('search-results'),
    resultEmpty: document.getElementById('search-empty'),
    resultCount: document.getElementById('result-count'),
    status: document.getElementById('search-status'),
    lines: document.getElementById('cart-lines'),
    cartCount: document.getElementById('cart-count'),
    cartField: document.getElementById('cart-field'),
    subtotal: document.getElementById('t-subtotal'),
    productDiscount: document.getElementById('t-product-discount'),
    customerDiscount: document.getElementById('t-customer-discount'),
    customerLabel: document.getElementById('t-customer-label'),
    tax: document.getElementById('t-tax'),
    total: document.getElementById('t-total'),
    submit: document.getElementById('complete-sale'),
    clear: document.getElementById('clear-cart'),
    methodField: document.getElementById('payment-method'),
    cashBlock: document.getElementById('cash-block'),
    amountPaid: document.getElementById('amount-paid'),
    changeNote: document.getElementById('change-note'),
    customerNumber: document.getElementById('customer-number'),
    customerStatus: document.getElementById('customer-status'),
    form: document.getElementById('checkout-form')
  };

  function money(value) {
    return config.currency + ' ' + Number(value).toFixed(2);
  }

  function icon(name, cls) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'icon ' + (cls || ''));
    svg.setAttribute('aria-hidden', 'true');
    var use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', '#i-' + name);
    svg.appendChild(use);
    return svg;
  }

  function say(message, tone) {
    if (!els.status) { return; }
    els.status.textContent = message || '';
    els.status.className = 'pos-search__status ' + (tone === 'error' ? 'small' : 'muted small');
    if (tone === 'error') { els.status.style.color = 'var(--danger-dark)'; }
    else { els.status.style.color = ''; }
  }

  function serialiseCart() {
    var parts = [];
    cart.forEach(function (line) { parts.push(line.id + ':' + line.qty); });
    return parts.join(',');
  }

  // ------------------------------------------------------------------ cart
  function addToCart(product) {
    var existing = cart.get(product.id);
    var nextQty = existing ? existing.qty + 1 : 1;
    if (product.stock > 0 && nextQty > product.stock) {
      say(product.name + ': only ' + product.stock + ' in stock.', 'error');
      return;
    }
    if (existing) { existing.qty = nextQty; }
    else { cart.set(product.id, Object.assign({}, product, { qty: 1 })); }
    say(product.name + ' added to the cart.');
    renderCart();
  }

  function setQuantity(id, qty) {
    var line = cart.get(id);
    if (!line) { return; }
    if (qty < 1) { cart.delete(id); renderCart(); return; }
    if (line.stock > 0 && qty > line.stock) {
      say(line.name + ': only ' + line.stock + ' in stock.', 'error');
      renderCart();
      return;
    }
    line.qty = qty;
    renderCart();
  }

  function thumb(line) {
    if (line.image) {
      var img = document.createElement('img');
      img.className = 'cart-line__thumb';
      img.src = line.image;
      img.alt = '';
      img.loading = 'lazy';
      return img;
    }
    var span = document.createElement('span');
    span.className = 'cart-line__thumb';
    span.textContent = (line.name || '?').trim().charAt(0).toUpperCase();
    return span;
  }

  function qtyControl(line) {
    var box = document.createElement('div');
    box.className = 'qty';

    var minus = document.createElement('button');
    minus.type = 'button';
    minus.className = 'qty__btn';
    minus.setAttribute('aria-label', 'Decrease ' + line.name);
    minus.appendChild(icon('minus', 'icon--sm'));
    minus.addEventListener('click', function () { setQuantity(line.id, line.qty - 1); });

    var input = document.createElement('input');
    input.type = 'number';
    input.className = 'qty__input';
    input.min = '1';
    input.value = line.qty;
    input.setAttribute('aria-label', 'Quantity of ' + line.name);
    input.addEventListener('change', function () {
      setQuantity(line.id, parseInt(input.value, 10) || 0);
    });

    var plus = document.createElement('button');
    plus.type = 'button';
    plus.className = 'qty__btn';
    plus.setAttribute('aria-label', 'Increase ' + line.name);
    plus.appendChild(icon('plus', 'icon--sm'));
    plus.addEventListener('click', function () { setQuantity(line.id, line.qty + 1); });

    box.appendChild(minus);
    box.appendChild(input);
    box.appendChild(plus);
    return box;
  }

  function renderCart() {
    els.lines.textContent = '';
    var units = 0;

    if (cart.size === 0) {
      var note = document.createElement('div');
      note.className = 'empty';
      note.style.padding = '32px 20px';
      var strong = document.createElement('strong');
      strong.textContent = 'Cart is empty';
      var hint = document.createElement('div');
      hint.textContent = 'Search for a product to start the sale.';
      note.appendChild(strong);
      note.appendChild(hint);
      els.lines.appendChild(note);
    }

    cart.forEach(function (line) {
      units += line.qty;

      var row = document.createElement('div');
      row.className = 'cart-line';
      row.appendChild(thumb(line));

      var text = document.createElement('div');
      text.className = 'cart-line__text';

      var name = document.createElement('div');
      name.className = 'cart-line__name';
      name.title = line.name;
      name.textContent = line.name;
      text.appendChild(name);

      var price = document.createElement('div');
      price.className = 'cart-line__price';
      if (Number(line.discount) > 0) {
        price.appendChild(document.createTextNode(money(line.final) + ' '));
        var was = document.createElement('s');
        was.textContent = money(line.price);
        price.appendChild(was);
        price.appendChild(document.createTextNode(' · ' + Number(line.discount) + '% off'));
      } else {
        price.textContent = money(line.price) + ' each';
      }
      text.appendChild(price);
      row.appendChild(text);

      var side = document.createElement('div');
      side.className = 'cart-line__side';
      side.appendChild(qtyControl(line));
      var total = document.createElement('div');
      total.className = 'cart-line__total';
      total.textContent = money(Number(line.final) * line.qty);
      side.appendChild(total);
      row.appendChild(side);

      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'cart-line__remove';
      remove.setAttribute('aria-label', 'Remove ' + line.name);
      remove.appendChild(icon('trash', 'icon--sm'));
      remove.addEventListener('click', function () { setQuantity(line.id, 0); });
      row.appendChild(remove);

      els.lines.appendChild(row);
    });

    els.cartCount.textContent = units + ' item' + (units === 1 ? '' : 's');
    els.cartField.value = serialiseCart();
    els.submit.disabled = cart.size === 0;
    scheduleQuote();
  }

  // --------------------------------------------------------------- totals
  function scheduleQuote() {
    window.clearTimeout(quoteTimer);
    quoteTimer = window.setTimeout(requestQuote, 150);
  }

  function requestQuote() {
    if (cart.size === 0) {
      [els.subtotal, els.productDiscount, els.customerDiscount, els.tax, els.total]
        .forEach(function (el) { el.textContent = money(0); });
      currentTotal = 0;
      updateChange();
      return;
    }

    var params = new URLSearchParams({
      cart: serialiseCart(),
      customer_number: els.customerNumber.value || ''
    });

    fetch(config.quoteUrl + '?' + params.toString(), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (data.error) { say(data.error, 'error'); return; }
        els.subtotal.textContent = money(data.subtotal);
        els.productDiscount.textContent = '- ' + money(data.product_discount);
        els.customerDiscount.textContent = '- ' + money(data.customer_discount);
        els.customerLabel.textContent = Number(data.customer_discount_percent) > 0
          ? 'Customer Discount (' + Number(data.customer_discount_percent) + '%)'
          : 'Customer Discount';
        els.tax.textContent = money(data.tax);
        els.total.textContent = money(data.total);
        currentTotal = Number(data.total);
        updateChange();
      })
      .catch(function () { say('Could not reach the server to price the cart.', 'error'); });
  }

  function updateChange() {
    if (paymentMethod !== 'CASH') {
      els.changeNote.textContent = 'The exact total will be captured.';
      return;
    }
    var paid = Number(els.amountPaid.value || 0);
    if (!paid) {
      els.changeNote.textContent = 'Change is worked out once there is a total.';
    } else if (paid < currentTotal) {
      els.changeNote.textContent = 'Short by ' + money(currentTotal - paid);
    } else {
      els.changeNote.textContent = 'Change: ' + money(paid - currentTotal);
    }
  }

  // ------------------------------------------------------- customer lookup
  function setCustomerStatus(text, cls) {
    els.customerStatus.textContent = '';
    var span = document.createElement('span');
    span.className = cls;
    span.textContent = text;
    els.customerStatus.appendChild(span);
  }

  function checkCustomer() {
    var raw = (els.customerNumber.value || '').trim();
    if (!raw) {
      setCustomerStatus(
        'Optional. Enter it to check for a returning customer.', 'muted small'
      );
      scheduleQuote();
      return;
    }

    fetch(config.customerUrl + '?number=' + encodeURIComponent(raw), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!data.valid) {
          setCustomerStatus(
            'Not a valid mobile number yet - for example 01712345678.', 'muted small'
          );
        } else if (data.existing) {
          setCustomerStatus(
            'Existing customer - ' + data.orders + ' previous order'
            + (data.orders === 1 ? '' : 's') + '. '
            + Number(data.discount_percent) + '% discount applied automatically.',
            'badge badge--success'
          );
        } else {
          setCustomerStatus('New customer - no customer discount.', 'badge');
        }
        scheduleQuote();
      })
      .catch(function () { say('Customer lookup is unavailable.', 'error'); });
  }

  // --------------------------------------------------------------- search
  function toProduct(item) {
    return {
      id: item.id,
      name: item.name,
      price: Number(item.price),
      discount: Number(item.discount_percent),
      final: Number(item.final_price),
      stock: item.stock,
      image: item.image_url || ''
    };
  }

  function highlight(index) {
    var rows = els.resultList.querySelectorAll('.result');
    if (!rows.length) { cursor = -1; return; }
    cursor = Math.max(0, Math.min(index, rows.length - 1));
    Array.prototype.forEach.call(rows, function (row, i) {
      row.classList.toggle('is-cursor', i === cursor);
      if (i === cursor && row.scrollIntoView) {
        row.scrollIntoView({ block: 'nearest' });
      }
    });
  }

  function renderResults(items) {
    results = items;
    cursor = -1;
    els.resultList.textContent = '';
    els.search.setAttribute('aria-expanded', items.length ? 'true' : 'false');

    if (!items.length) {
      els.resultEmpty.hidden = false;
      els.resultEmpty.innerHTML =
        '<div class="empty">'
        + '<div class="empty__icon"><svg class="icon icon--lg" aria-hidden="true">'
        + '<use href="#i-search"></use></svg></div>'
        + '<strong>No products match that search</strong>'
        + '<div>Check the spelling, or try the SKU instead.</div></div>';
      els.resultCount.textContent = '';
      return;
    }

    els.resultEmpty.hidden = true;
    els.resultCount.textContent = items.length + ' product' + (items.length === 1 ? '' : 's');

    items.forEach(function (item) {
      var row = document.createElement('button');
      row.type = 'button';
      row.className = 'result';
      row.setAttribute('role', 'option');
      if (item.stock <= 0) { row.disabled = true; }

      if (item.image_url) {
        var img = document.createElement('img');
        img.className = 'result__media';
        img.src = item.image_url;
        img.alt = '';
        img.loading = 'lazy';
        row.appendChild(img);
      } else {
        var blank = document.createElement('span');
        blank.className = 'result__media';
        blank.textContent = item.initials || '?';
        row.appendChild(blank);
      }

      var text = document.createElement('span');
      text.className = 'result__text';
      var name = document.createElement('span');
      name.className = 'result__name';
      name.textContent = item.name;
      var meta = document.createElement('span');
      meta.className = 'result__meta';
      meta.textContent = item.sku + ' · ' + item.category + ' · '
        + (item.stock <= 0
            ? 'Out of stock'
            : item.stock + ' ' + item.unit.toLowerCase() + ' in stock');
      text.appendChild(name);
      text.appendChild(meta);
      row.appendChild(text);

      var price = document.createElement('span');
      price.className = 'result__price';
      var strong = document.createElement('b');
      strong.textContent = money(item.final_price);
      price.appendChild(strong);
      if (Number(item.discount_percent) > 0) {
        price.appendChild(document.createElement('br'));
        var was = document.createElement('s');
        was.textContent = money(item.price);
        price.appendChild(was);
      }
      row.appendChild(price);

      row.addEventListener('click', function () {
        addToCart(toProduct(item));
        els.search.focus();
      });
      els.resultList.appendChild(row);
    });
  }

  function search(term) {
    term = (term || '').trim();
    if (term.length < 2) {
      results = [];
      els.resultList.textContent = '';
      els.resultCount.textContent = '';
      els.resultEmpty.hidden = false;
      els.resultEmpty.innerHTML =
        '<div class="empty">'
        + '<div class="empty__icon"><svg class="icon icon--lg" aria-hidden="true">'
        + '<use href="#i-search"></use></svg></div>'
        + '<strong>Nothing searched yet</strong>'
        + '<div>Type at least two characters, or scan a barcode to add it '
        + 'straight to the cart.</div></div>';
      return;
    }

    fetch(config.lookupUrl + '?q=' + encodeURIComponent(term), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        // A scanned barcode matches exactly one product: add it straight away
        // so the scanner never needs a second keystroke.
        if (data.results.length === 1 && data.results[0].barcode === term) {
          addToCart(toProduct(data.results[0]));
          els.search.value = '';
          els.search.select();
          renderResults([]);
          return;
        }
        renderResults(data.results);
      })
      .catch(function () { say('Product search is unavailable.', 'error'); });
  }

  // ----------------------------------------------------------------- wire
  els.search.addEventListener('input', function () {
    window.clearTimeout(searchTimer);
    var term = els.search.value;
    searchTimer = window.setTimeout(function () { search(term); }, 220);
  });

  els.search.addEventListener('keydown', function (event) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      highlight(cursor + 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      highlight(cursor - 1);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      window.clearTimeout(searchTimer);
      if (cursor >= 0 && results[cursor]) {
        if (results[cursor].stock > 0) { addToCart(toProduct(results[cursor])); }
        return;
      }
      // Nothing highlighted: run the search, which also handles a scan.
      search(els.search.value);
    } else if (event.key === 'Escape') {
      els.search.value = '';
      renderResults([]);
      search('');
    }
  });

  els.customerNumber.addEventListener('input', function () {
    window.clearTimeout(customerTimer);
    customerTimer = window.setTimeout(checkCustomer, 350);
  });
  els.customerNumber.addEventListener('blur', checkCustomer);

  Array.prototype.forEach.call(document.querySelectorAll('.pay-method'), function (button) {
    button.addEventListener('click', function () {
      Array.prototype.forEach.call(document.querySelectorAll('.pay-method'), function (other) {
        other.classList.remove('is-active');
        other.setAttribute('aria-pressed', 'false');
      });
      button.classList.add('is-active');
      button.setAttribute('aria-pressed', 'true');
      paymentMethod = button.dataset.method;
      els.methodField.value = paymentMethod;
      els.cashBlock.hidden = paymentMethod !== 'CASH';
      updateChange();
    });
  });

  els.amountPaid.addEventListener('input', updateChange);

  els.clear.addEventListener('click', function () {
    if (cart.size && !window.confirm('Clear the whole cart?')) { return; }
    cart.clear();
    els.customerNumber.value = '';
    checkCustomer();
    renderCart();
    els.search.focus();
  });

  els.form.addEventListener('submit', function (event) {
    if (cart.size === 0) {
      event.preventDefault();
      return;
    }
    if (paymentMethod === 'CASH') {
      var paid = Number(els.amountPaid.value || 0);
      if (paid < currentTotal) {
        event.preventDefault();
        say('Cash received is less than the total.', 'error');
        els.amountPaid.focus();
        return;
      }
    }
    // Guard against a double click resubmitting the same sale. The server has
    // its own one-shot token as well; this is only the friendly half.
    els.submit.disabled = true;
    els.submit.textContent = 'Processing…';
  });

  renderCart();
  if (els.search.value.trim()) { search(els.search.value); }
}());
