window.SuppAList = (function () {
  function row(type, item, detailUrl) {
    const tr = document.createElement("tr");

    const tdTitle = document.createElement("td");

    const a = document.createElement("a");

    /*
     * recordUrl is the current fetchable URL:
     * - GitHub Raw URL today
     * - real SuppA API URL in the future
     *
     * id remains available as the canonical JSON-LD URI, but is not used
     * as the fetch target until etc.web.ox.ac.uk serves the documents.
     */
    const params = new URLSearchParams({
      type: type,
      recordUrl: item.recordUrl
    });

    if (item.id) {
      params.set("etcUri", item.id);
    }

    if (item.derivedFrom) {
      params.set("id", item.derivedFrom);
    }

    a.href = `${detailUrl}?${params.toString()}`;
    a.textContent = item.title || item.etcIdentifier || item.identifier;

    tdTitle.appendChild(a);
    tr.appendChild(tdTitle);

    const tdDate = document.createElement("td");
    tdDate.textContent = item.date || item.dates || "";
    tr.appendChild(tdDate);

    const tdId = document.createElement("td");
    tdId.textContent = item.etcIdentifier || item.identifier || "";
    tr.appendChild(tdId);

    return tr;
  }

  function render(type, container, jsonUrl) {
    const detailUrl = container.dataset.detailUrl;

    fetch(jsonUrl, {
      headers: { Accept: "application/json" }
    })
      .then(response => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return response.json();
      })
      .then(items => {
        if (!items.length) {
          container.innerHTML =
            '<p class="has-text-grey">No records found.</p>';
          return;
        }

        const table = document.createElement("table");
        table.className =
          "table is-fullwidth is-hoverable is-striped";

        table.innerHTML = `
          <thead>
            <tr>
              <th>Title</th>
              <th>Date</th>
              <th>Identifier</th>
            </tr>
          </thead>
        `;

        const tbody = document.createElement("tbody");

        items.forEach(item => {
          tbody.appendChild(row(type, item, detailUrl));
        });

        table.appendChild(tbody);
        container.appendChild(table);
      })
      .catch(error => {
        console.error(`Failed to load SuppA ${type} index:`, error);

        container.innerHTML =
          '<p class="has-text-danger">Failed to load records.</p>';
      });
  }

  return { render };
})();