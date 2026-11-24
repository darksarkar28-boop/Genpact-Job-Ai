for (const section of document.querySelectorAll("[data-formset]")) {
  const prefix = section.dataset.formset;
  const total = section.querySelector('[name="' + prefix + '-TOTAL_FORMS"]');
  const limit = Number(section.querySelector('[name="' + prefix + '-MAX_NUM_FORMS"]').value);
  const button = section.querySelector("[data-add]");
  button.hidden = false;
  button.disabled = Number(total.value) >= limit;
  button.addEventListener("click", () => {
    const index = Number(total.value);
    if (index >= limit) return;
    const template = section.querySelector("template");
    const rows = section.querySelector("[data-rows]");
    rows.insertAdjacentHTML("beforeend", template.innerHTML.replaceAll("__prefix__", String(index)));
    total.value = String(index + 1);
    button.disabled = index + 1 >= limit;
    rows.lastElementChild.querySelector("input").focus();
  });
}
