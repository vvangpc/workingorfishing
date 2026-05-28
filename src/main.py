"""入口：QApplication + 组件装配 + 信号连接 + 单实例守护。"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

import time
from pathlib import Path

from .ai_classifier import AIClassifier
from .classifier import Classifier
from .collector import Collector
from .data_io import (
    WebDAVClient,
    export_to_zip,
    import_from_zip,
    webdav_pull,
    webdav_push,
)
from .floating_window import FloatingWindow
from .main_window import MainWindow
from .paths import db_file, rules_file, settings_file
from .settings import Settings
from .single_instance import try_acquire
from .storage import Storage
from .tray import Tray


def _configure_logging() -> None:
    from .paths import data_root
    log_file = data_root() / "app.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler()],
    )


def main() -> int:
    _configure_logging()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("WorkingorFishing")

    # 单实例守护：拿不到锁就退出（已通知已有实例前台显示）
    singleton = try_acquire()
    if singleton is None:
        logging.info("another instance is running; exiting")
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "WorkingorFishing", "系统托盘不可用，程序无法运行。")
        return 1

    settings = Settings.load()
    storage = Storage()
    classifier = Classifier()
    ai = AIClassifier(settings.ai)
    if settings.ai.enabled:
        ai.start()

    collector = Collector(storage, classifier)
    collector.configure(settings.sample_interval_seconds, settings.idle_threshold_seconds)

    tray = Tray()
    floating = FloatingWindow(settings, storage)
    main_win = MainWindow(storage, classifier, ai, settings)

    tray.update_click_through(settings.floating_window.click_through)

    # --- 状态 / 采样信号 ---

    def on_state(state: str) -> None:
        tray.update_state(state)
        floating.update_state(state)
        main_win.overview.update_status(state)

    def on_unknown(sample: dict) -> None:
        ai.enqueue(sample)
        main_win.overview.update_current_sample(
            sample.get("process"), sample.get("title"), sample.get("url")
        )
        main_win.overview.refresh_pending_count()

    def on_inserted() -> None:
        main_win.overview.on_record_inserted()
        floating.on_record_inserted()

    collector.state_changed.connect(on_state)
    collector.unknown_sample.connect(on_unknown)
    collector.record_inserted.connect(on_inserted)

    # --- 显示窗口 ---

    def show_main() -> None:
        main_win.show_and_raise()
        main_win.jump_to_overview()

    def show_stats() -> None:
        main_win.show_and_raise()
        main_win.jump_to_stats()

    def toggle_floating() -> None:
        if floating.isVisible():
            floating.hide()
        else:
            floating.show()

    def toggle_click_through() -> None:
        new = not settings.floating_window.click_through
        settings.floating_window.click_through = new
        settings.save()
        floating._apply_click_through(new)
        tray.update_click_through(new)
        main_win.settings_tab.reload_from_settings()

    def toggle_pause() -> None:
        new_paused = not settings.paused
        settings.paused = new_paused
        settings.save()
        collector.set_paused(new_paused)
        tray.update_paused(new_paused)

    def quit_app() -> None:
        collector.stop()
        ai.stop()
        storage.close()
        try:
            floating.close_overlay()
        except Exception:
            pass
        try:
            singleton.stop()
        except Exception:
            pass
        app.quit()

    # --- 托盘 / 悬浮窗信号 ---
    tray.show_main.connect(show_main)
    tray.show_stats.connect(show_stats)
    tray.toggle_floating.connect(toggle_floating)
    tray.toggle_click_through.connect(toggle_click_through)
    tray.toggle_pause.connect(toggle_pause)
    tray.quit_requested.connect(quit_app)

    floating.request_show_main.connect(show_main)
    floating.request_show_stats.connect(show_stats)
    floating.request_toggle_pause.connect(toggle_pause)
    floating.request_toggle_click_through.connect(toggle_click_through)
    floating.request_quit.connect(quit_app)

    # --- 主窗口信号 ---
    main_win.pause_requested.connect(toggle_pause)

    def on_settings_changed(s: Settings) -> None:
        collector.configure(s.sample_interval_seconds, s.idle_threshold_seconds)
        floating.apply_settings(s)
        floating.set_sample_interval(s.sample_interval_seconds)
        tray.update_click_through(s.floating_window.click_through)
        main_win.stats_widget.set_sample_interval(s.sample_interval_seconds)
        main_win.overview.set_sample_interval(s.sample_interval_seconds)
        ai.configure(s.ai)
        if s.ai.enabled:
            ai.start()
        else:
            ai.stop()

    main_win.settings_changed.connect(on_settings_changed)

    def on_rules_changed() -> None:
        collector.clear_unknown_seen()
        main_win.stats_widget.refresh_all()

    main_win.rules_changed.connect(on_rules_changed)

    # --- 数据管理 ---

    def refresh_data_ui() -> None:
        main_win.overview.refresh_bars()
        main_win.overview.refresh_pending_count()
        main_win.stats_widget.refresh_all()
        main_win.rules_widget.refresh()
        main_win.settings_tab.reload_from_settings()

    def on_export(zip_path: str) -> None:
        try:
            included = export_to_zip(
                Path(zip_path), db_file(), settings_file(), rules_file(),
            )
        except Exception as e:
            QMessageBox.warning(main_win, "导出失败", str(e))
            return
        QMessageBox.information(
            main_win, "导出完成",
            f"已导出 {len(included)} 个文件 → {zip_path}",
        )

    def on_import(zip_path: str) -> None:
        # 停采集 + 关 AI + 关 DB → 替换 → 重启
        was_paused = settings.paused
        collector.stop()
        ai.stop()
        storage.close()
        try:
            extracted = import_from_zip(
                Path(zip_path), db_file(), settings_file(), rules_file(),
            )
        except Exception as e:
            storage.reopen()
            if not was_paused:
                collector.start()
            QMessageBox.warning(main_win, "导入失败", str(e))
            return

        storage.reopen()
        settings.apply_from_file()
        classifier.reload(force=True)
        ai.configure(settings.ai)
        if settings.ai.enabled:
            ai.start()
        collector.configure(settings.sample_interval_seconds, settings.idle_threshold_seconds)
        floating.apply_settings(settings)
        if not settings.paused:
            collector.start()
        refresh_data_ui()
        QMessageBox.information(
            main_win, "导入完成",
            f"已恢复 {len(extracted)} 个文件：{', '.join(extracted)}",
        )

    def on_clear() -> None:
        n = storage.clear_all_activity()
        collector.clear_unknown_seen()
        refresh_data_ui()
        QMessageBox.information(main_win, "已清除", f"已删除 {n} 条活动记录。")

    def _make_webdav() -> WebDAVClient | None:
        wd = settings.webdav
        if not wd.url or not wd.username:
            QMessageBox.warning(main_win, "WebDAV 未配置", "请先填好地址 / 用户名 / 密码并保存。")
            return None
        try:
            return WebDAVClient(wd.url, wd.username, wd.password)
        except Exception as e:
            QMessageBox.warning(main_win, "WebDAV 客户端创建失败", str(e))
            return None

    def on_webdav_test() -> None:
        c = _make_webdav()
        if c is None:
            return
        ok, msg = c.test()
        if ok:
            QMessageBox.information(main_win, "WebDAV 测试", msg)
        else:
            QMessageBox.warning(main_win, "WebDAV 测试失败", msg)

    def on_webdav_push() -> None:
        c = _make_webdav()
        if c is None:
            return
        # 推送前先 flush 一下 WAL，确保 activity.db 文件包含最新写入
        try:
            storage._conn.execute("PRAGMA wal_checkpoint(FULL)")
        except Exception:
            pass
        ok, messages = webdav_push(c, db_file(), settings_file(), rules_file())
        if ok:
            settings.webdav.last_push = time.time()
            settings.save()
            main_win.settings_tab.refresh_sync_status()
        text = "\n".join(messages)
        if ok:
            QMessageBox.information(main_win, "推送完成", text)
        else:
            QMessageBox.warning(main_win, "部分文件推送失败", text)

    def on_webdav_pull() -> None:
        c = _make_webdav()
        if c is None:
            return
        was_paused = settings.paused
        collector.stop()
        ai.stop()
        storage.close()

        ok, messages = webdav_pull(c, db_file(), settings_file(), rules_file())

        storage.reopen()
        settings.apply_from_file()
        classifier.reload(force=True)
        ai.configure(settings.ai)
        if settings.ai.enabled:
            ai.start()
        collector.configure(settings.sample_interval_seconds, settings.idle_threshold_seconds)
        floating.apply_settings(settings)
        if not settings.paused:
            collector.start()

        if ok:
            settings.webdav.last_pull = time.time()
            settings.save()
        refresh_data_ui()
        text = "\n".join(messages)
        if ok:
            QMessageBox.information(main_win, "拉取完成", text)
        else:
            QMessageBox.warning(main_win, "部分文件拉取失败", text)

    main_win.settings_tab.request_export.connect(on_export)
    main_win.settings_tab.request_import.connect(on_import)
    main_win.settings_tab.request_clear.connect(on_clear)
    main_win.settings_tab.request_webdav_test.connect(on_webdav_test)
    main_win.settings_tab.request_webdav_push.connect(on_webdav_push)
    main_win.settings_tab.request_webdav_pull.connect(on_webdav_pull)

    # 另一实例尝试启动时把主窗口拉到前台
    singleton.new_instance_requested.connect(show_main)

    # --- 启动 ---
    if settings.floating_window.enabled:
        floating.show()
    if settings.paused:
        collector.set_paused(True)
        tray.update_paused(True)
    else:
        collector.start()

    tray.show_message("WorkingorFishing", "已启动，正在监控前台窗口")

    if not classifier.get_rules():
        QTimer.singleShot(500, show_main)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
