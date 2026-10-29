const DEFAULTS = {
  retries: 3,
  timeout: 1200,
  labels: ["new", "blocked", "active", "done"],
};

const escapeHtml = (value) => String(value).replace(/[&<>"']/g, (char) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]);

function normalizeTask(task) {
  return {
    id: String(task.id),
    title: task.title.trim(),
    status: task.status ?? "new",
    tags: [...new Set(task.tags ?? [])],
    priority: task.priority ?? 0,
  };
}

function groupTasks(tasks, { includeDone = false } = {}) {
  const groups = new Map();
  for (const task of tasks) {
    const item = normalizeTask(task);
    if (!includeDone && item.status === "done") continue;
    const group = groups.get(item.status) ?? [];
    group.push(item);
    groups.set(item.status, group);
  }
  for (const group of groups.values()) {
    group.sort((a, b) => b.priority - a.priority || a.title.localeCompare(b.title));
  }
  return groups;
}

function renderTask(task) {
  return `<li data-id="${escapeHtml(task.id)}">${escapeHtml(task.title)}</li>`;
}

function renderGroups(groups) {
  return [...groups.entries()]
    .map(([status, tasks]) => `<section data-status="${escapeHtml(status)}">
      <h2>${escapeHtml(status)}</h2>
      <ul>${tasks.map(renderTask).join("\n")}</ul>
    </section>`)
    .join("\n");
}

export { DEFAULTS, groupTasks, renderGroups };
