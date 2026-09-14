/* Copyright (c) 2025-2026 CyberoniOntoni
 *
 * Permission to use, copy, modify, and/or distribute this
 * software for any purpose with or without fee is hereby granted,
 * provided that the above copyright notice and this permission
 * notice appear in all copies.
 */

#pragma once
#include "api/plugin-api.hpp"
#include "options/options.hpp"
#include "ui_smoothtrack-controls.h"
#include "adb_client.h"

#include <QMutex>
#include <QTcpSocket>
#include <QTcpServer>
#include <QThread>
#include <cmath>
#include <memory>

#if defined(OPENTRACK_SMOOTHTRACK_HAVE_USBMUXD)
extern "C" {
#include <usbmuxd.h>
}
#endif

using namespace options;

enum device_platform
{
    PLATFORM_IOS = 0,
    PLATFORM_ANDROID = 1,
};

struct settings : opts
{
    value<int> platform;
    value<int> port;           // iOS port (47047)
    value<int> android_port;   // Android port (4242)
    value<QString> adb_path;   // Custom ADB executable path
    value<int> add_yaw, add_pitch, add_roll;

    settings()
        : opts("smoothtrack-tracker")
        , platform(b, "platform", PLATFORM_IOS)
        , port(b, "port", 47047)
        , android_port(b, "android-port", 4242)
        , adb_path(b, "adb-path", "")
        , add_yaw(b, "add-yaw", 0)
        , add_pitch(b, "add-pitch", 0)
        , add_roll(b, "add-roll", 0)
    {
    }
};

class smoothtrack : protected QThread, public ITracker
{
    Q_OBJECT
public:
    smoothtrack();
    ~smoothtrack() override;
    module_status start_tracker(QFrame*) override;
    void data(double* data) override;

protected:
    void run() override;

private:
    module_status start_ios();
    module_status start_android();

    QTcpSocket sock;
    QTcpServer server;
    std::unique_ptr<adb_client> adb;
    double last_recv_pose[6]{};
    QMutex mutex;
    settings s;
};

class dialog_smoothtrack : public ITrackerDialog
{
    Q_OBJECT
public:
    dialog_smoothtrack();
    void register_tracker(ITracker*) override {}
    void unregister_tracker() override {}

private:
    Ui::UISmoothTrackControls ui;
    settings s;
private slots:
    void doOK();
    void doCancel();
};

class smoothtrack_metadata : public Metadata
{
    Q_OBJECT

    QString name() { return tr("SmoothTrack (USB)"); }
    QIcon icon() { return QIcon(":/images/opentrack.png"); }
};
