#!/usr/bin/env python3
"""Refactor app.css to clean tokens, zero external hex, <= 5 !important, and full components.

Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import re
from pathlib import Path

APP_CSS_PATH = Path("src/nextlife/api/static/app.css")


def refactor_css() -> None:
    txt = APP_CSS_PATH.read_text(encoding="utf-8")

    # 1. New root block
    new_root = """:root {
  /* Бренд и основное действие */
  --c-brand:         #801432;
  --c-brand-hover:   #6a1029;
  --c-brand-soft:    #fbeef2;
  --c-accent:        #801432;
  --c-accent-hov:    #6a1029;
  --c-accent-light:  #fbeef2;

  /* Ссылки (единственное применение синего цвета) */
  --c-link:          #2563eb;
  --c-link-hover:    #1d4ed8;

  /* Поверхности */
  --c-bg:            #f8fafc;
  --c-surface:       #ffffff;
  --c-surface-2:     #f1f5f9;
  --c-surface-3:     #e2e8f0;
  --c-surface-white: #ffffff;
  --c-surface-soft:  #f8fafc;

  /* Границы */
  --c-border:        #e2e8f0;
  --c-border-strong: #cbd5e1;
  --c-border-md:     #cbd5e1;

  /* Текст */
  --c-text:          #0f172a;
  --c-text-2:        #475569;
  --c-text-3:        #94a3b8;
  --c-text-muted:    #475569;
  --c-text-faint:    #94a3b8;

  /* Статусы */
  --c-success:       #10b981;
  --c-success-bg:    #ecfdf5;
  --c-success-bdr:   #a7f3d0;
  --c-success-text:  #065f46;
  --c-success-dk:    #047857;

  --c-warning:       #f59e0b;
  --c-warning-bg:    #fffbeb;
  --c-warning-bdr:   #fde68a;
  --c-warning-text:  #92400e;
  --c-warning-dk:    #b45309;

  --c-danger:        #ef4444;
  --c-danger-bg:     #fef2f2;
  --c-danger-bdr:    #fecaca;
  --c-danger-text:   #b91c1c;

  --c-info:          #3b82f6;
  --c-info-bg:       #eff6ff;
  --c-info-bdr:      #bfdbfe;
  --c-info-text:     #1d4ed8;

  --c-ink:           #1e293b;
  --c-ink-2:         #334155;
  --c-ink-text:      #e2e8f0;
  --c-ink-text-2:    #cbd5e1;

  /* Отступы */
  --sp-1: 4px;
  --sp-2: 8px;
  --sp-3: 12px;
  --sp-4: 16px;
  --sp-5: 24px;
  --sp-6: 32px;
  --sp-8: 48px;

  /* Типографика */
  --fs-micro: 11px;
  --fs-xs:   12px;
  --fs-sm:   13px;
  --fs-base: 14px;
  --fs-h2:   15px;
  --fs-h1:   18px;
  --fs-lg:   20px;

  /* Скругления */
  --r-sm:   6px;
  --r-md:   8px;
  --r-lg:   10px;
  --r-full: 9999px;

  /* Тени */
  --shadow-1: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, 0.06);
  --shadow-md: 0 4px 12px rgba(128, 20, 50, 0.08);

  /* Высоты элементов интерфейса */
  --h-control: 36px;
  --h-btn-sm:  32px;
  --h-btn-xs:  28px;
  --h-nav:     56px;
  --h-header:  48px;
}"""

    # Replace root block
    txt = re.sub(r':root\s*\{[^}]*\}', new_root, txt, flags=re.DOTALL)

    # 2. Replace hardcoded hex colors outside root
    # Common replacements:
    replacements = [
        ("color: #fff;", "color: var(--c-surface-white);"),
        ("color: #ffffff;", "color: var(--c-surface-white);"),
        ("color: #ffffff !important;", "color: var(--c-surface-white);"),
        ("background: #fff;", "background: var(--c-surface);"),
        ("background: #ffffff;", "background: var(--c-surface);"),
        ("outline: 2px solid #fff;", "outline: 2px solid var(--c-surface-white);"),
        ("background: #4ade80;", "background: var(--c-success);"),
        ("background: #ef4444;", "background: var(--c-danger);"),
        ("background: #f1f5f9;", "background: var(--c-surface-2);"),
        ("color: #334155;", "color: var(--c-text-2);"),
        ("background: #e2e8f0;", "background: var(--c-border);"),
        ("color: #0f172a;", "color: var(--c-text);"),
        ("background: #2563eb;", "background: var(--c-brand);"),
        ("color: #2e7d32;", "color: var(--c-success-dk);"),
        ("var(--c-accent, #2563eb)", "var(--c-brand)"),
        ("var(--c-border, #e2e8f0)", "var(--c-border)"),
        ("var(--c-surface-soft, #f8fafc)", "var(--c-surface-soft)"),
        ("var(--c-text-muted, #475569)", "var(--c-text-muted)"),
        ("var(--c-text, #0f172a)", "var(--c-text)"),
        ("var(--c-surface-2, #f4f5f7)", "var(--c-surface-2)"),
    ]

    for old, new in replacements:
        txt = txt.replace(old, new)

    # 3. Clean up .card-actions-bar & .listing-card-status-* (remove all their !important and hex)
    old_card_actions = re.search(r'/\* Card Bottom Horizontal Action Bar.*?(?=/\* Inputs & Forms \*/|\.card-link\b)', txt, flags=re.DOTALL)
    if old_card_actions:
        new_card_actions = """/* Card Bottom Horizontal Action Bar (§Cards Actions Footer) */
.card-actions-bar {
  display: flex;
  width: 100%;
  margin-top: auto;
  border-top: 1px solid var(--c-border);
  background: var(--c-surface-soft);
  border-bottom-left-radius: inherit;
  border-bottom-right-radius: inherit;
  overflow: hidden;
  box-sizing: border-box;
  padding: 0;
}

.card-actions-bar > .card-action-btn,
.card-actions-bar > form {
  flex: 1 1 0%;
  min-width: 0;
  margin: 0;
  padding: 0;
  display: flex;
}

.card-actions-bar > form > button,
.card-actions-bar > a.card-action-btn,
.card-actions-bar > button.card-action-btn {
  flex: 1 1 0%;
  width: 100%;
  height: 38px;
  min-height: 38px;
  max-height: 38px;
  padding: 0;
  margin: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 0;
  background: transparent;
  color: var(--c-text-muted);
  cursor: pointer;
  text-decoration: none;
  box-sizing: border-box;
  transition: background 0.15s ease, color 0.15s ease;
}

.card-actions-bar > *:not(:last-child) {
  border-right: 1px solid var(--c-border);
}

.card-actions-bar a.card-action-btn:hover,
.card-actions-bar form button:hover,
.card-actions-bar button.card-action-btn:hover {
  background: rgba(0, 0, 0, 0.05);
  color: var(--c-text);
}

.card-actions-bar .action-danger {
  color: var(--c-danger-text);
}
.card-actions-bar .action-danger:hover {
  background: var(--c-danger-bg);
  color: var(--c-danger-text);
}
.card-actions-bar .action-primary {
  color: var(--c-link);
}
.card-actions-bar .action-primary:hover {
  background: var(--c-info-bg);
  color: var(--c-link-hover);
}
.card-actions-bar .action-success {
  color: var(--c-success-text);
}
.card-actions-bar .action-success:hover {
  background: var(--c-success-bg);
  color: var(--c-success-text);
}

/* --- Listing Card Status Color Schemes --- */
.listing-card-status-draft {
  background-color: var(--c-warning-bg);
  border: 1px solid var(--c-warning-bdr);
}
.listing-card-status-draft .card-actions-bar {
  background-color: var(--c-warning-bg);
  border-top: 1px solid var(--c-warning-bdr);
}
.listing-card-status-draft .card-actions-bar > *:not(:last-child) {
  border-right: 1px solid var(--c-warning-bdr);
}

.listing-card-status-approved {
  background-color: var(--c-success-bg);
  border: 1px solid var(--c-success-bdr);
}
.listing-card-status-approved .card-actions-bar {
  background-color: var(--c-success-bg);
  border-top: 1px solid var(--c-success-bdr);
}
.listing-card-status-approved .card-actions-bar > *:not(:last-child) {
  border-right: 1px solid var(--c-success-bdr);
}

.listing-card-status-published {
  background-color: var(--c-info-bg);
  border: 1px solid var(--c-info-bdr);
}
.listing-card-status-published .card-actions-bar {
  background-color: var(--c-info-bg);
  border-top: 1px solid var(--c-info-bdr);
}
.listing-card-status-published .card-actions-bar > *:not(:last-child) {
  border-right: 1px solid var(--c-info-bdr);
}

.listing-card-status-error {
  background-color: var(--c-danger-bg);
  border: 1px solid var(--c-danger-bdr);
}
.listing-card-status-error .card-actions-bar {
  background-color: var(--c-danger-bg);
  border-top: 1px solid var(--c-danger-bdr);
}
.listing-card-status-error .card-actions-bar > *:not(:last-child) {
  border-right: 1px solid var(--c-danger-bdr);
}

.listing-card-status-archived {
  background-color: var(--c-surface-2);
  border: 1px solid var(--c-border-strong);
}
.listing-card-status-archived .card-actions-bar {
  background-color: var(--c-surface-2);
  border-top: 1px solid var(--c-border-strong);
}
.listing-card-status-archived .card-actions-bar > *:not(:last-child) {
  border-right: 1px solid var(--c-border-strong);
}
"""
        txt = txt[:old_card_actions.start()] + new_card_actions + txt[old_card_actions.end():]

    # 4. Remove leftover !important from stepper and media queries
    txt = txt.replace("cursor: default !important;", "cursor: default;")
    txt = txt.replace("background: transparent !important;", "background: transparent;")
    txt = txt.replace("cursor: pointer !important;", "cursor: pointer;")
    txt = txt.replace("background: #2563eb !important;", "background: var(--c-brand);")
    txt = txt.replace("font-weight: 600 !important;", "font-weight: 600;")
    txt = txt.replace("box-shadow: none !important;", "box-shadow: none;")
    txt = txt.replace("padding: 8px 0 80px 0 !important;", "padding: 8px 0 80px 0;")
    txt = txt.replace("font-size: 0.88rem !important;", "font-size: 0.88rem;")

    # 5. Ensure allowed !important have comments
    txt = re.sub(r'\.hidden\s*\{\s*display:\s*none\s*!important;\s*\}', '.hidden { display: none !important; /* utility hide override */ }', txt)
    txt = re.sub(r'\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}', '[hidden] { display: none !important; /* HTML5 hidden attribute override */ }', txt)
    txt = re.sub(r'\.tasks-drawer-overlay\.hidden\s*\{\s*display:\s*none\s*!important;\s*\}', '.tasks-drawer-overlay.hidden { display: none !important; /* overlay toggle override */ }', txt)
    txt = re.sub(r'\.wz-searching\[hidden\]\s*\{\s*display:\s*none\s*!important;\s*\}', '.wz-searching[hidden] { display: none; }', txt)

    # 6. Add component standards classes to end of file
    components_css = """
/* ==========================================================================
   Единая компактная дизайн-система (TZ §3.1, §3.2, §3.3)
   ========================================================================== */

/* Базовые кнопки */
.btn {
  height: 36px;
  padding: 0 12px;
  font-size: 14px;
  font-weight: 600;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid transparent;
  cursor: pointer;
  text-decoration: none;
  box-sizing: border-box;
  transition: all 0.15s ease;
  white-space: nowrap;
}
.btn-primary {
  background: var(--c-brand);
  color: var(--c-surface-white);
  border-color: var(--c-brand);
}
.btn-primary:hover {
  background: var(--c-brand-hover);
  border-color: var(--c-brand-hover);
}
.btn-secondary {
  background: var(--c-surface);
  color: var(--c-text);
  border: 1px solid var(--c-border-strong);
}
.btn-secondary:hover {
  background: var(--c-surface-2);
}
.btn-danger {
  background: var(--c-danger-bg);
  color: var(--c-danger-text);
  border: 1px solid var(--c-danger-bdr);
}
.btn-danger:hover {
  background: var(--c-danger);
  color: var(--c-surface-white);
}
.btn-ghost {
  background: transparent;
  color: var(--c-text-2);
}
.btn-ghost:hover {
  background: var(--c-surface-2);
  color: var(--c-text);
}
.btn-sm {
  height: 32px;
  padding: 0 10px;
  font-size: 13px;
  border-radius: 6px;
}
.btn-xs {
  height: 28px;
  padding: 0 8px;
  font-size: 12px;
  border-radius: 6px;
  position: relative;
}
.btn-xs::after {
  content: '';
  position: absolute;
  top: -4px; bottom: -4px;
  left: 0; right: 0;
}
.btn-icon {
  width: 36px;
  height: 36px;
  min-width: 36px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  background: transparent;
  border: 1px solid var(--c-border);
  color: var(--c-text-2);
  cursor: pointer;
}
.btn-icon:hover {
  background: var(--c-surface-2);
  color: var(--c-text);
}

/* Поля ввода и селекты */
.input, .select, input[type="text"], input[type="search"], input[type="number"], input[type="password"], select {
  height: 36px;
  padding: 0 10px;
  font-size: 14px;
  color: var(--c-text);
  background: var(--c-surface);
  border: 1px solid var(--c-border-strong);
  border-radius: 6px;
  box-sizing: border-box;
  font-family: inherit;
}
.input:focus, .select:focus, input:focus, select:focus {
  outline: none;
  border-color: var(--c-brand);
  box-shadow: 0 0 0 2px var(--c-brand-soft);
}
textarea.input, textarea {
  padding: 8px 10px;
  font-size: 14px;
  color: var(--c-text);
  background: var(--c-surface);
  border: 1px solid var(--c-border-strong);
  border-radius: 6px;
  box-sizing: border-box;
  font-family: inherit;
}

/* Заголовки страниц и тулбары */
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 36px;
  margin-bottom: var(--sp-2);
  gap: var(--sp-2);
}
.page-head h1 {
  font-size: var(--fs-h1);
  font-weight: 600;
  color: var(--c-text);
  margin: 0;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-bottom: var(--sp-3);
  width: 100%;
}
.toolbar-search {
  flex: 1;
  min-width: 160px;
}

/* Табы и сегменты */
.tabs {
  display: flex;
  gap: var(--sp-1);
  overflow-x: auto;
  scrollbar-width: none;
  margin-bottom: var(--sp-3);
  padding-bottom: 2px;
}
.tabs::-webkit-scrollbar { display: none; }
.tab-item {
  height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--c-surface-2);
  color: var(--c-text-2);
  text-decoration: none;
  white-space: nowrap;
  cursor: pointer;
  border: none;
}
.tab-item:hover {
  background: var(--c-border);
  color: var(--c-text);
}
.tab-item.active {
  background: var(--c-brand);
  color: var(--c-surface-white);
  font-weight: 600;
}
.tab-counter {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.08);
}
.tab-item.active .tab-counter {
  background: rgba(255, 255, 255, 0.25);
  color: var(--c-surface-white);
}

.seg {
  display: inline-flex;
  background: var(--c-surface-2);
  border-radius: 6px;
  padding: 2px;
  height: 28px;
  align-items: center;
}
.seg-item {
  height: 24px;
  padding: 0 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--c-text-2);
  text-decoration: none;
  cursor: pointer;
  border: none;
  background: transparent;
}
.seg-item.active {
  background: var(--c-brand);
  color: var(--c-surface-white);
  font-weight: 600;
}

/* Чипы и переключатели */
.chip {
  height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: var(--c-surface-2);
  color: var(--c-text-2);
  text-decoration: none;
  white-space: nowrap;
}
.chip.active {
  background: var(--c-brand);
  color: var(--c-surface-white);
}
.chip-toggle {
  height: 28px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: var(--c-surface);
  border: 1px solid var(--c-border-strong);
  color: var(--c-text-2);
  cursor: pointer;
  user-select: none;
}
.chip-toggle input { display: none; }
.chip-toggle.is-checked {
  background: var(--c-brand-soft);
  border-color: var(--c-brand);
  color: var(--c-brand);
  font-weight: 600;
}

/* Бейджи */
.badge {
  height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  white-space: nowrap;
}
.badge-success { background: var(--c-success-bg); color: var(--c-success-text); }
.badge-warning { background: var(--c-warning-bg); color: var(--c-warning-text); }
.badge-danger  { background: var(--c-danger-bg);  color: var(--c-danger-text); }
.badge-info    { background: var(--c-info-bg);    color: var(--c-info-text); }
.badge-neutral { background: var(--c-surface-2);  color: var(--c-text-2); }

/* Карточки и строки */
.card {
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: 10px;
  padding: 12px;
  box-shadow: var(--shadow-1);
}
.list-row {
  min-height: 64px;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: 8px 12px;
  border-bottom: 1px solid var(--c-border);
  background: var(--c-surface);
  transition: background 0.15s ease;
}
.list-row:hover {
  background: var(--c-surface-2);
}
.list-row-thumb {
  width: 48px;
  height: 48px;
  flex-shrink: 0;
  border-radius: 6px;
  overflow: hidden;
  background: var(--c-surface-2);
  display: flex;
  align-items: center;
  justify-content: center;
}
.thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.thumb-placeholder {
  color: var(--c-text-3);
  display: flex;
  align-items: center;
  justify-content: center;
}
.list-row-body {
  flex: 1;
  min-width: 0;
}
.list-row-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--c-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.list-row-meta {
  font-size: 12px;
  color: var(--c-text-2);
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.list-row-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.list-row-price {
  font-weight: 700;
  color: var(--c-success-dk);
  font-size: 14px;
}

/* Слим-полоса */
.slim-bar {
  height: 32px;
  padding: 0 10px;
  border-radius: 6px;
  background: var(--c-info-bg);
  color: var(--c-info-text);
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  margin-bottom: var(--sp-2);
}
.slim-bar-close {
  color: var(--c-info-text);
  text-decoration: none;
  font-weight: 700;
  padding: 2px 6px;
  cursor: pointer;
}

/* Выпадающее меню переполнения */
.menu {
  position: relative;
  display: inline-block;
}
.menu summary { list-style: none; }
.menu summary::-webkit-details-marker { display: none; }
.menu-dropdown {
  position: absolute;
  right: 0;
  top: 100%;
  margin-top: 4px;
  background: var(--c-surface);
  border: 1px solid var(--c-border);
  border-radius: 6px;
  box-shadow: var(--shadow-md);
  min-width: 150px;
  z-index: 100;
  padding: 4px 0;
}
.menu-item {
  display: block;
  width: 100%;
  padding: 8px 12px;
  font-size: 13px;
  color: var(--c-text);
  text-align: left;
  border: none;
  background: none;
  cursor: pointer;
  text-decoration: none;
}
.menu-item:hover {
  background: var(--c-surface-2);
}
.menu-item.text-danger {
  color: var(--c-danger-text);
}
.menu-divider {
  border: 0;
  border-top: 1px solid var(--c-border);
  margin: 4px 0;
}

/* Поповер подсказки */
.hint {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: 999px;
  background: var(--c-surface-2);
  color: var(--c-text-2);
  font-size: 11px;
  font-weight: 700;
  cursor: help;
  position: relative;
}
.hint-popover {
  display: none;
  position: absolute;
  bottom: 120%;
  left: 50%;
  transform: translateX(-50%);
  background: var(--c-ink);
  color: var(--c-surface-white);
  padding: 6px 10px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 400;
  white-space: normal;
  width: 220px;
  z-index: 200;
}
.hint:hover .hint-popover, .hint:focus .hint-popover {
  display: block;
}

/* Нижняя панель действий */
.action-bar {
  position: sticky;
  bottom: calc(var(--h-nav) + env(safe-area-inset-bottom));
  height: 44px;
  background: var(--c-surface);
  border-top: 1px solid var(--c-border);
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--sp-2);
  padding: 0 var(--sp-3);
  z-index: 40;
}

/* Пустое состояние */
.empty {
  height: 44px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--c-text-3);
  font-size: 13px;
  text-align: center;
}

/* Модальное окно и шторка */
.sheet {
  border: none;
  border-radius: 12px 12px 0 0;
  background: var(--c-surface);
  padding: var(--sp-4);
  max-width: 500px;
  width: 100%;
  box-shadow: var(--shadow-md);
}
.sheet::backdrop {
  background: rgba(15, 23, 42, 0.4);
}

/* Темная тема для фотостенда (Focus Mode) */
.focus-mode {
  --c-bg: #0f172a;
  --c-surface: #1e293b;
  --c-surface-2: #334155;
  --c-border: #334155;
  --c-border-strong: #475569;
  --c-text: #f8fafc;
  --c-text-2: #94a3b8;
  --c-text-3: #64748b;
  background: var(--c-bg);
  color: var(--c-text);
  min-height: 100vh;
}

/* Десктопная адаптивность (TZ §3.3) */
.desktop-sidebar {
  display: none;
}

@media (min-width: 1024px) {
  .app-shell {
    max-width: 1200px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
  }
  .app-body-layout {
    display: flex;
    gap: var(--sp-4);
    align-items: flex-start;
  }
  .desktop-sidebar {
    display: flex;
    flex-direction: column;
    width: 200px;
    flex-shrink: 0;
    gap: 4px;
    padding: var(--sp-3) 0;
  }
  .desktop-nav-item {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 14px;
    font-weight: 500;
    color: var(--c-text-2);
    text-decoration: none;
    transition: all 0.15s ease;
  }
  .desktop-nav-item:hover {
    background: var(--c-surface-2);
    color: var(--c-text);
  }
  .desktop-nav-item.active {
    background: var(--c-brand);
    color: var(--c-surface-white);
    font-weight: 600;
  }
  .desktop-nav-item svg {
    width: 18px;
    height: 18px;
  }
  .app-main-content {
    flex: 1;
    min-width: 0;
  }
  .bottom-nav {
    display: none !important; /* hidden on desktop per §3.3 */
  }
  body {
    padding-bottom: var(--sp-4);
  }
}
"""

    txt += components_css
    APP_CSS_PATH.write_text(txt, encoding="utf-8")
    print("Refactored app.css written.")


if __name__ == "__main__":
    refactor_css()
