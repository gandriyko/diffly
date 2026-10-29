const DEFAULTS = {
  retries: 2,
  timeout: 1200,
  labels: ["new", "active", "done"],
};

function normalizeTask(task) {
  return {
    id: String(task.id),
    title: task.title.trim(),
    status: task.status || "new",
    tags: task.tags || [],
  };
}

function groupTasks(tasks) {
  const groups = new Map();
  for (const task of tasks) {
    const item = normalizeTask(task);
    const group = groups.get(item.status) || [];
    group.push(item);
    groups.set(item.status, group);
  }
  return groups;
}

function renderTask(task) {
  return `<li data-id="${task.id}">${task.title}</li>`;
}

function renderGroups(groups) {
  return [...groups.entries()]
    .map(([status, tasks]) => `<section data-status="${status}">
      <h2>${status}</h2>
      <ul>${tasks.map(renderTask).join("")}</ul>
    </section>`)
    .join("\n");
}

export { DEFAULTS, groupTasks, renderGroups };
