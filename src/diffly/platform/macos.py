"""Native macOS titlebar toolbar backed by the existing Qt actions."""

import sys
from ctypes import c_void_p

if sys.platform == "darwin":
    import AppKit
    import objc
    from diffly.gui.icons import native_icon_png

    class _DifflyToolbarDelegate(AppKit.NSObject):
        def toolbarAllowedItemIdentifiers_(self, toolbar):
            return list(self.identifiers)

        def toolbarDefaultItemIdentifiers_(self, toolbar):
            return list(self.identifiers)

        def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(self, toolbar, identifier, inserted):
            action = self.actions[identifier]
            item = AppKit.NSToolbarItem.alloc().initWithItemIdentifier_(identifier)
            item.setLabel_(action.text())
            item.setPaletteLabel_(action.text())
            item.setToolTip_(action.toolTip())
            png = native_icon_png(self.symbols[identifier])
            data = AppKit.NSData.dataWithBytes_length_(png, len(png))
            image = AppKit.NSImage.alloc().initWithData_(data)
            if image is not None:
                image.setSize_(AppKit.NSMakeSize(24, 24))
                image.setTemplate_(True)
                item.setImage_(image)
            item.setTarget_(self)
            item.setAction_("activate:")
            item.setEnabled_(action.isEnabled())
            self.items[identifier] = item
            return item

        def activate_(self, sender):
            action = self.actions.get(sender.itemIdentifier())
            if action is not None and action.isEnabled():
                action.trigger()

        def validateToolbarItem_(self, item):
            action = self.actions.get(item.itemIdentifier())
            return action is not None and action.isEnabled()


class NativeToolbar:
    def __init__(self, qt_window, delegate, toolbar, native_window):
        self.qt_window = qt_window
        self.delegate = delegate
        self.toolbar = toolbar
        self.native_window = native_window
        self._suppress_mouse_up = False
        mask = AppKit.NSEventMaskLeftMouseDown | AppKit.NSEventMaskLeftMouseUp
        self._event_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask, self._handle_mouse_event
        )

    def _handle_mouse_event(self, event):
        if event.window() != self.native_window:
            return event
        if self._suppress_mouse_up and event.type() == AppKit.NSEventTypeLeftMouseUp:
            self._suppress_mouse_up = False
            return None
        if event.type() != AppKit.NSEventTypeLeftMouseDown or event.clickCount() != 2:
            return event
        point = event.locationInWindow()
        content = self.native_window.contentLayoutRect()
        if point.y <= content.origin.y + content.size.height:
            return event
        title_view = self.native_window.contentView().superview().hitTest_(point)
        # AppKit owns the titlebar and toolbar hit targets. Only consume a
        # double-click on its title area; native toolbar items keep their clicks.
        if title_view is None or "Title" not in type(title_view).__name__:
            return event
        if self.qt_window.isMaximized():
            self.qt_window.showNormal()
        else:
            self.qt_window.showMaximized()
        self._suppress_mouse_up = True
        return None

    def sync(self):
        for identifier, item in self.delegate.items.items():
            item.setEnabled_(self.delegate.actions[identifier].isEnabled())
        self.toolbar.validateVisibleItems()

    def close(self):
        if self._event_monitor is not None:
            AppKit.NSEvent.removeMonitor_(self._event_monitor)
            self._event_monitor = None


def install_inline_toolbar(qt_window, action_map: dict) -> NativeToolbar | None:
    """Attach an NSToolbar inline with the title, or leave the Qt toolbar intact."""
    if sys.platform != "darwin":
        return None

    from PySide6.QtWidgets import QApplication

    if QApplication.instance().platformName() != "cocoa":
        return None

    view = objc.objc_object(c_void_p=c_void_p(int(qt_window.winId())))
    native_window = view.window()
    if native_window is None:
        return None

    delegate = _DifflyToolbarDelegate.alloc().init()
    delegate.actions = {identifier: action for identifier, (action, _symbol) in action_map.items()}
    delegate.symbols = {identifier: symbol for identifier, (_action, symbol) in action_map.items()}
    identifiers = []
    for identifier in action_map:
        if identifiers:
            identifiers.append(AppKit.NSToolbarSpaceItemIdentifier)
        identifiers.append(identifier)
    delegate.identifiers = tuple(identifiers)
    delegate.items = {}
    toolbar = AppKit.NSToolbar.alloc().initWithIdentifier_("com.diffly.merge")
    toolbar.setDelegate_(delegate)
    toolbar.setAllowsUserCustomization_(False)
    toolbar.setDisplayMode_(AppKit.NSToolbarDisplayModeIconOnly)
    native_window.setToolbar_(toolbar)
    native_window.setToolbarStyle_(AppKit.NSWindowToolbarStyleUnifiedCompact)
    if native_window.toolbar() is None:
        return None
    bridge = NativeToolbar(qt_window, delegate, toolbar, native_window)
    bridge.sync()
    return bridge
