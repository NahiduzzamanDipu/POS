/* POS terminal: cart handling, live search, customer lookup and totals.
   Money is never computed here - the server returns the authoritative quote,
   and decides customer eligibility again when the sale is submitted. */
(function () {
  'use strict';

  var config = window.POS_CONFIG || {};
  var cart = new Map();          // productId -> {id, name, price, discount, stock, qty}
  var paymentMethod = 'CASH';
  var quoteTimer = null;
  var searchTimer = null;
  var customerTimer = null;
  var currentTotal = 0;

  var els = {
    grid: document.getElementById('product-grid'),
    gridNote: document.getElementById('grid-note'),
    search: document.getElementById('product-search'),
    lines: document.getElementById('cart-lines'),
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
      flash(product.name + ': only ' + product.stock + ' in stock.');
      return;
    }
    if (existing) {
      existing.qty = nextQty;
    } else {
      cart.set(product.id, Object.assign({}, product, { qty: 1 }));
    }
    render();
  }

  function setQuantity(id, qty) {
    var line = cart.get(id);
    if (!line) { return; }
    if (qty < 1) { cart.delete(id); render(); return; }
    if (line.stock > 0 && qty > line.stock) {
      flash(line.name + ': only ' + line.stock + ' in stock.');
      return;
    }
    line.qty = qty;
    render();
  }

  function flash(message) {
    if (!els.gridNote) { return; }
    els.gridNote.textContent = message;
    window.clearTimeout(els.gridNote._timer);
    els.gridNote._timer = window.setTimeout(function () {
      els.gridNote.textContent = '';
    }, 4000);
  }

  function render() {
    els.lines.textContent = '';

    if (cart.size === 0) {
      var note = document.createElement('p');
      note.className = 'muted small';
      note.textContent = 'Scan or click a product to start the sale.';
      els.lines.appendChild(note);
    }

    cart.forEach(function (line) {
      var row = document.createElement('div');
      row.className = 'cart-line';

      var left = document.createElement('div');
      left.style.display = 'flex';
      left.style.alignItems = 'center';
      left.style.gap = '10px';

      if (line.image) {
        var thumb = document.createElement('img');
        thumb.className = 'cart-line__thumb';
        thumb.src = line.image;
        thumb.alt = '';
        thumb.loading = 'lazy';
        left.appendChild(thumb);
      }

      var text = document.createElement('div');
      var name = document.createElement('div');
      name.className = 'cart-line__name';
      name.textContent = line.name;
      text.appendChild(name);

      var price = document.createElement('div');
      price.className = 'cart-line__price';
      if (Number(line.discount) > 0) {
        price.textContent = money(line.final) + '  ';
        var was = document.createElement('s');
        was.className = 'muted';
        was.textContent = money(line.price);
        price.appendChild(was);
        var tag = document.createElement('span');
        tag.className = 'badge badge--success';
        tag.style.marginLeft = '6px';
        tag.textContent = Number(line.discount) + '% off';
        price.appendChild(tag);
      } else {
        price.textContent = money(line.price);
      }
      text.appendChild(price);
      left.appendChild(text);

      var qtyBox = document.createElement('div');
      qtyBox.className = 'cart-line__qty';
      qtyBox.appendChild(qtyButton('-', function () { setQuantity(line.id, line.qty - 1); }, 'Decrease ' + line.name));
      var value = document.createElement('span');
      value.className = 'qty-value';
      value.textContent = line.qty;
      qtyBox.appendChild(value);
      qtyBox.appendChild(qtyButton('+', function () { setQuantity(line.id, line.qty + 1); }, 'Increase ' + line.name));

      var remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'cart-line__remove';
      remove.textContent = 'Remove';
      remove.addEventListener('click', function () { setQuantity(line.id, 0); });

      row.appendChild(left);
      row.appendChild(qtyBox);
      row.appendChild(document.createElement('span'));
      row.appendChild(remove);
      els.lines.appendChild(row);
    });

    els.cartField.value = serialiseCart();
    els.submit.disabled = cart.size === 0;
    scheduleQuote();
  }

  function qtyButton(label, handler, ariaLabel) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'qty-btn';
    button.textContent = label;
    button.setAttribute('aria-label', ariaLabel);
    button.addEventListener('click', handler);
    return button;
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
        if (data.error) { flash(data.error); return; }
        els.subtotal.textContent = money(data.subtotal);
        els.productDiscount.textContent = money(data.product_discount);
        els.customerDiscount.textContent = money(data.customer_discount);
        els.customerLabel.textContent = Number(data.customer_discount_percent) > 0
          ? 'Customer Discount (' + Number(data.customer_discount_percent) + '%)'
          : 'Customer Discount';
        els.tax.textContent = money(data.tax);
        els.total.textContent = money(data.total);
        currentTotal = Number(data.total);
        updateChange();
      })
      .catch(function () { flash('Could not reach the server to price the cart.'); });
  }

  function updateChange() {
    if (paymentMethod !== 'CASH') {
      els.changeNote.textContent = 'The exact total will be captured.';
      return;
    }
    var paid = Number(els.amountPaid.value || 0);
    if (!paid) {
      els.changeNote.textContent = 'Change will be calculated after the total.';
    } else if (paid < currentTotal) {
      els.changeNote.textContent = 'Short by ' + money(currentTotal - paid);
    } else {
      els.changeNote.textContent = 'Change: ' + money(paid - currentTotal);
    }
  }

  // ------------------------------------------------------- customer lookup
  function setCustomerStatus(html, cls) {
    els.customerStatus.textContent = '';
    var span = document.createElement('span');
    span.className = cls;
    span.textContent = html;
    els.customerStatus.appendChild(span);
  }

  function checkCustomer() {
    var raw = (els.customerNumber.value || '').trim();
    if (!raw) {
      setCustomerStatus(
        'Optional. Enter the number to check for a returning customer.', 'muted small'
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
            + (data.orders === 1 ? '' : 's')
            + '. ' + Number(data.discount_percent) + '% discount applied automatically.',
            'badge badge--success'
          );
        } else {
          setCustomerStatus('New customer - no customer discount.', 'badge');
        }
        scheduleQuote();
      })
      .catch(function () { flash('Customer lookup is unavailable.'); });
  }

  // --------------------------------------------------------------- search
  function bindTile(tile) {
    tile.addEventListener('click', function () {
      addToCart({
        id: Number(tile.dataset.id),
        name: tile.dataset.name,
        price: Number(tile.dataset.price),
        discount: Number(tile.dataset.discount || 0),
        final: Number(tile.dataset.final || tile.dataset.price),
        stock: Number(tile.dataset.stock),
        image: tile.dataset.image || ''
      });
    });
  }

  function renderResults(results) {
    els.grid.textContent = '';
    if (!results.length) {
      var none = document.createElement('p');
      none.className = 'muted';
      none.textContent = 'No products match that search.';
      els.grid.appendChild(none);
      return;
    }
    results.forEach(function (item) {
      var tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'product-tile';
      tile.dataset.id = item.id;
      tile.dataset.name = item.name;
      tile.dataset.price = item.price;
      tile.dataset.discount = item.discount_percent;
      tile.dataset.final = item.final_price;
      tile.dataset.stock = item.stock;
      tile.dataset.image = item.image_url || '';
      if (item.stock <= 0) { tile.disabled = true; }

      if (item.image_url) {
        var media = document.createElement('img');
        media.className = 'product-tile__media';
        media.src = item.image_url;
        media.alt = '';
        media.loading = 'lazy';
        tile.appendChild(media);
      } else {
        var blank = document.createElement('span');
        blank.className = 'product-tile__media product-tile__media--empty';
        blank.textContent = item.initials || '?';
        tile.appendChild(blank);
      }

      var name = document.createElement('span');
      name.className = 'product-tile__name';
      name.textContent = item.name;

      var price = document.createElement('span');
      price.className = 'product-tile__price';
      price.textContent = money(item.final_price);
      if (Number(item.discount_percent) > 0) {
        var was = document.createElement('s');
        was.className = 'muted small';
        was.style.marginLeft = '6px';
        was.textContent = money(item.price);
        price.appendChild(was);
      }

      var meta = document.createElement('span');
      meta.className = 'product-tile__meta';
      meta.textContent = item.stock <= 0
        ? 'Out of stock'
        : item.stock + ' ' + item.unit.toLowerCase() + ' in stock';

      tile.appendChild(name);
      tile.appendChild(price);
      tile.appendChild(meta);
      bindTile(tile);
      els.grid.appendChild(tile);
    });
  }

  function search(term) {
    fetch(config.lookupUrl + '?q=' + encodeURIComponent(term), {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        // A scanned barcode matches exactly one product: add it straight away.
        if (data.results.length === 1 && data.results[0].barcode === term.trim()) {
          var hit = data.results[0];
          addToCart({
            id: hit.id, name: hit.name, price: Number(hit.price),
            discount: Number(hit.discount_percent), final: Number(hit.final_price),
            stock: hit.stock, image: hit.image_url || ''
          });
          els.search.value = '';
          els.search.select();
          return;
        }
        renderResults(data.results);
      })
      .catch(function () { flash('Product search is unavailable.'); });
  }

  // ----------------------------------------------------------------- wire
  Array.prototype.forEach.call(els.grid.querySelectorAll('.product-tile'), bindTile);

  els.search.addEventListener('input', function () {
    window.clearTimeout(searchTimer);
    var term = els.search.value;
    searchTimer = window.setTimeout(function () { search(term); }, 220);
  });

  els.search.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {
      event.preventDefault();
      window.clearTimeout(searchTimer);
      search(els.search.value);
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
      });
      button.classList.add('is-active');
      paymentMethod = button.dataset.method;
      els.methodField.value = paymentMethod;
      els.cashBlock.style.display = paymentMethod === 'CASH' ? '' : 'none';
      updateChange();
    });
  });

  els.amountPaid.addEventListener('input', updateChange);

  els.clear.addEventListener('click', function () {
    if (cart.size && !window.confirm('Clear the whole cart?')) { return; }
    cart.clear();
    els.customerNumber.value = '';
    checkCustomer();
    render();
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
        flash('Cash received is less than the total.');
        return;
      }
    }
    // Guard against a double click resubmitting the same sale.
    els.submit.disabled = true;
    els.submit.textContent = 'Processing...';
  });

  render();
}());
