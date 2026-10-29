# Diffly — Technical Specification

- **Project Name:** Diffly
- **Version:** 0.2
- **Status:** Draft
- **Platforms:** macOS, Linux
- **Technology:** Python 3.11+, PySide6, Qt 6
- **Primary Priorities:** Performance, Responsiveness, Reliability, Extensibility

## 1. Project Overview

**Diffly** — кросплатформний графічний застосунок для швидкого порівняння, редагування та інтерактивного об'єднання текстових файлів.

Програма має два незалежні редактори з підсвічуванням синтаксису та відмінностей, можливістю переносити зміни між файлами в обох напрямках і підтримкою фонових обчислень без блокування інтерфейсу.

У майбутньому Diffly підтримуватиме рекурсивне порівняння директорій із вкладеними підпапками.

## 2. Project Identity

| Parameter | Value |
|---|---|
| Application Name | Diffly |
| Repository | `diffly` |
| Python Package | `diffly` |
| CLI Command | `diffly` |
| macOS Application | `Diffly.app` |
| Linux Application | `diffly` |
| Specification | `SPEC.md` |

## 3. Technical Architecture

**Основний стек:**

- Python 3.11+
- PySide6 / Qt 6 Widgets
- QPlainTextEdit для першої версії
- QSyntaxHighlighter для синтаксису
- QThreadPool для фонових I/O-завдань
- ProcessPoolExecutor для CPU-intensive Python-операцій
- Незалежний Diff Engine із можливістю заміни на Rust/C++
- pytest та pytest-qt для тестування

### Project Structure

```text
diffly/
├── pyproject.toml
├── SPEC.md
├── README.md
├── src/
│   └── diffly/
│       ├── app.py
│       ├── gui/
│       │   ├── main_window.py
│       │   ├── editor_widget.py
│       │   ├── diff_gutter.py
│       │   └── syntax_highlighter.py
│       ├── core/
│       │   ├── documents/
│       │   ├── diff/
│       │   └── directories/
│       ├── services/
│       │   ├── task_scheduler.py
│       │   ├── file_service.py
│       │   └── comparison_service.py
│       └── platform/
│           ├── macos.py
│           └── linux.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── gui/
│   └── benchmarks/
└── docs/
    ├── architecture.md
    └── performance.md
```

## 4. Functional Scope

### MVP — File Comparison

- Два незалежні редаговані файли поруч.
- Підсвічування рядкових відмінностей.
- Двостороннє перенесення diff-блоків.
- Автоматичний перерахунок diff після редагування.
- Undo/Redo.
- Відкриття та безпечне збереження файлів.
- Асинхронний diff без блокування GUI.
- Контроль версій документів для запобігання застосуванню застарілих змін.

### Advanced File Comparison

- Підсвічування синтаксису.
- Відмінності всередині рядків.
- Синхронна прокрутка.
- Перенесення вибраних рядків.
- Пошук і заміна.
- Оптимізація великих файлів.

### Future — Directory Comparison

- Рекурсивне порівняння директорій.
- Порівняння вкладених підпапок.
- Фільтри файлів та ignore patterns.
- Відображення Added / Removed / Modified / Identical.
- Фонове сканування.
- Відкриття відмінних файлів у двопанельному редакторі.
- Контроль пам'яті та паралельності.

## 5. Performance Requirements

**Продуктивність та відсутність блокування інтерфейсу — головні технічні пріоритети Diffly.**

- Жодні тривалі операції не виконуються в GUI-потоці.
- CPU-intensive Python-операції можуть виконуватися в окремих процесах.
- Фонові задачі мають пріоритети, обмежену кількість worker-ів та механізм скасування.
- Diff-результати прив'язуються до revision обох документів.
- Застарілі результати не застосовуються.
- Для великих файлів передбачено часткове завантаження, кешування та віртуалізований перегляд.
- Високопродуктивне ядро Rust/C++ можна підключити без заміни GUI.

### Target Performance

| Metric | Target |
|---|---|
| Input latency | p95 < 30 ms |
| Scroll frame time | p95 < 33 ms |
| Diff debounce | 200–400 ms |
| Small file opening (до 1 MB) | < 500 ms |
| Background task cancellation | Cooperative |
| Memory consumption | Bounded where feasible |

Ці значення є цілями для тестування, а не гарантованими результатами для всіх систем.

## 6. Architecture Principles

1. GUI не залежить від конкретного diff-алгоритму.
2. Diff Engine не залежить від PySide6.
3. Документи мають незалежні моделі та лічильники ревізій.
4. Обидва редактори підтримують незалежне редагування та Undo/Redo.
5. Усі тривалі операції виконуються поза головним потоком.
6. Результати фонових задач перевіряються перед застосуванням.
7. File Comparison і Directory Comparison використовують спільні сервіси.
8. Перенесення diff-блоків має бути атомарною логічною операцією.
9. Збереження файлів повинно бути безпечним.
10. Оптимізації впроваджуються на основі профілювання та бенчмарків.

## 7. Development Roadmap

**Phase 1 — MVP:** два редактори, відкриття файлів, редагування, line diff, перенесення блоків, Undo/Redo, збереження, фонові обчислення.

**Phase 1.5 — Advanced Comparison:** підсвічування синтаксису, intra-line diff, синхронна прокрутка, вибіркове перенесення рядків.

**Phase 2 — Directory Comparison:** рекурсивне сканування, дерево файлів, фільтри, порівняння вмісту, фонові задачі.

**Phase 3 — Large File Optimization:** віртуалізований редактор, mmap, chunk-based storage, incremental diff, можливе ядро Rust/C++.

## 8. MVP Acceptance Criteria

MVP Diffly вважається готовим, коли користувач може відкрити два файли, незалежно редагувати їх, бачити актуальні відмінності, переносити цілі diff-блоки в обидва боки, скасовувати операції через Undo, безпечно зберігати файли та продовжувати працювати з інтерфейсом під час фонового обчислення diff.

Архітектура повинна залишати можливість подальшого додавання рекурсивного порівняння директорій без переписування редактора.
