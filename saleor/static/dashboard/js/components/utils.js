import 'select2';

let supportsPassive = false;
try {
  let opts = Object.defineProperty({}, 'passive', {
    get: function () {
      supportsPassive = true;
    }
  });
  window.addEventListener('test', null, opts);
} catch (e) {
}

function appendOption ($select, option) {
  $select.append($('<option></option>')
    .attr('value', option.id).text(option.text));
}

export function onScroll (func) {
  window.addEventListener('scroll', func, supportsPassive ? {passive: true} : false);
}

export function initSelects () {
  $('select:not(.browser-default):not(.enable-ajax-select2):not([multiple])').material_select();
  $('select[multiple]:not(.browser-default):not(.enable-ajax-select2)').select2({width: '100%'});

  $('select.enable-ajax-select2').each((i, select) => {
    const $select = $(select);
    const initial = $select.data('initial');

    if (initial) {
      const initialData = initial instanceof Array ? initial : [initial];
      const selected = initialData.map((item) => {
        appendOption($select, item);
        return (item.id);
      });
      $select.val(selected);
    }

    $select.select2({
      ajax: {
        url: $select.data('url'),
        delay: 250
      },
      width: '100%',
      minimumInputLength: 2
    });
  });
}
