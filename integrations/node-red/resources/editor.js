/* Shared editor helpers for the OpenAVC nodes: fetch a list from the deployed
 * server node, and turn a text field into a pick-or-type field over it. */
(function () {
  function serverId() {
    return $("#node-input-server").val();
  }

  // done(list) on success, done(null, reason) otherwise. The reason is shown
  // in the dialog so a lookup that cannot run says why instead of going quiet.
  function fetchList(path, done) {
    const id = serverId();
    if (!id || id === "_ADD_") {
      done(null, "Pick a server and deploy it, then reopen this dialog to load the list.");
      return;
    }
    $.getJSON("openavc/" + encodeURIComponent(id) + "/" + path)
      .done(function (data) {
        done(data);
      })
      .fail(function (xhr) {
        const err = (xhr.responseJSON && xhr.responseJSON.error) || "Could not reach the OpenAVC system.";
        done(null, err);
      });
  }

  // items: [{label, value}]. Typing still works when the list is empty.
  function autocomplete($input, items) {
    if ($input.data("ui-autocomplete")) $input.autocomplete("destroy");
    $input
      .autocomplete({
        minLength: 0,
        source: function (req, res) {
          const q = (req.term || "").toLowerCase();
          res(
            items.filter(function (i) {
              return i.label.toLowerCase().indexOf(q) >= 0 || i.value.toLowerCase().indexOf(q) >= 0;
            })
          );
        },
        focus: function () {
          return false;
        },
        select: function (_e, ui) {
          $input.val(ui.item.value).trigger("change");
          return false;
        },
      })
      .on("focus", function () {
        $(this).autocomplete("search", "");
      });
  }

  function hint($el, text) {
    $el.text(text || "").toggle(!!text);
  }

  window.openavcEditor = { fetchList: fetchList, autocomplete: autocomplete, hint: hint };
})();
