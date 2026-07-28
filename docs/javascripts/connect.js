document.querySelectorAll(".amh-copy-block").forEach((block, index) => {
  const code = block.matches("code") ? block : block.querySelector("code");
  const container = block.closest("pre") || block;
  if (!code || container.querySelector(".amh-doc-copy")) return;

  const button = document.createElement("button");
  button.type = "button";
  button.className = "amh-doc-copy";
  button.textContent = "Copy";
  button.setAttribute("aria-label", `Copy setup snippet ${index + 1}`);
  container.prepend(button);

  button.addEventListener("click", () => {
    navigator.clipboard.writeText(code.textContent || "").then(() => {
      button.textContent = "Copied";
      button.classList.add("is-copied");
      window.setTimeout(() => {
        button.textContent = "Copy";
        button.classList.remove("is-copied");
      }, 1400);
    });
  });
});
