/* Copyright (c) 2025

 * Permission to use, copy, modify, and/or distribute this
 * software for any purpose with or without fee is hereby granted,
 * provided that the above copyright notice and this permission
 * notice appear in all copies.
 */

#include "ftnoir_tracker_smoothtrack.h"
#include "api/plugin-api.hpp"

#include <QDebug>
#include <cmath>
#include <cstddef>
#include <iterator>

#ifdef _WIN32
#   include <winsock2.h>
static void close_native_fd(qintptr fd)
{
    if (fd >= 0)
        ::closesocket(static_cast<SOCKET>(fd));
}
#else
#   include <unistd.h>
static void close_native_fd(qintptr fd)
{
    if (fd >= 0)
        ::close(static_cast<int>(fd));
}
#endif

smoothtrack::smoothtrack() = default;

smoothtrack::~smoothtrack()
{
    requestInterruption();
    wait();
}

static qintptr connect_usbmuxd(uint16_t port, QString* error_detail)
{
    usbmuxd_device_info_t* device_list = nullptr;
    const int device_count = usbmuxd_get_device_list(&device_list);

    if (device_count < 1)
    {
        if (device_list)
            usbmuxd_device_list_free(&device_list);
        if (error_detail)
            *error_detail = QObject::tr("No iOS device found via usbmuxd");
        return -1;
    }

    const uint32_t device_handle = device_list[0].handle;
    usbmuxd_device_list_free(&device_list);

    const int fd = usbmuxd_connect(device_handle, port);
    if (fd < 0)
    {
        if (error_detail)
            *error_detail = QObject::tr("usbmuxd_connect failed for port %1 (code %2)")
                                .arg(port)
                                .arg(fd);
        return -1;
    }

    return static_cast<qintptr>(fd);
}

module_status smoothtrack::start_tracker(QFrame*)
{
    QString detail;
    const qintptr fd = connect_usbmuxd(static_cast<uint16_t>(int(s.port)), &detail);
    if (fd < 0)
    {
        qDebug() << "smoothtrack:" << detail;
        return error(tr("Can't connect to SmoothTrack over USB.\n"
                        "1. SmoothTrack is running on the iOS device\n"
                        "2. Device is connected via USB and trusted\n"
                        "3. usbmuxd / Apple Mobile Device Support is available\n"
                        "4. Port matches SmoothTrack USB settings (%1)\n"
                        "%2")
                         .arg(int(s.port))
                         .arg(detail));
    }

    // QAbstractSocket takes ownership of the descriptor on success.
    if (!sock.setSocketDescriptor(fd))
    {
        const QString err = sock.errorString();
        qDebug() << "smoothtrack: setSocketDescriptor failed:" << err;
        close_native_fd(fd);
        return error(tr("Can't attach socket to usbmuxd connection — %1").arg(err));
    }

    sock.moveToThread(this);
    start();
    return status_ok();
}

void smoothtrack::run()
{
    while (!isInterruptionRequested())
    {
        if (!sock.waitForReadyRead(100))
        {
            if (sock.state() != QAbstractSocket::ConnectedState)
            {
                qDebug() << "smoothtrack: socket disconnected:" << sock.errorString();
                break;
            }
            continue;
        }

        while (sock.bytesAvailable() >= static_cast<qint64>(sizeof(double[6])))
        {
            double pose[6]{};
            const qint64 sz = sock.read(reinterpret_cast<char*>(pose), sizeof(pose));

            if (sz != static_cast<qint64>(sizeof(pose)))
                break;

            bool ok = true;
            for (unsigned i = 0; i < 6; i++)
            {
                const int val = std::fpclassify(pose[i]);
                if (val == FP_NAN || val == FP_INFINITE)
                {
                    ok = false;
                    break;
                }
            }

            if (ok)
            {
                QMutexLocker lock(&mutex);
                for (unsigned i = 0; i < 6; i++)
                    last_recv_pose[i] = pose[i];
            }
        }
    }

    sock.abort();
}

void smoothtrack::data(double* data)
{
    QMutexLocker lock(&mutex);
    for (int i = 0; i < 6; i++)
        data[i] = last_recv_pose[i];

    const int values[] = {
        0,
        90,
        -90,
        180,
        -180,
    };
    const int indices[] = {
        s.add_yaw,
        s.add_pitch,
        s.add_roll,
    };

    for (int i = 0; i < 3; i++)
    {
        const int k = indices[i];
        if (k >= 0 && static_cast<std::size_t>(k) < std::size(values))
            data[Yaw + i] += values[k];
    }
}

OPENTRACK_DECLARE_TRACKER(smoothtrack, dialog_smoothtrack, smoothtrack_metadata)
