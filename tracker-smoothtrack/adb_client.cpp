/* Copyright (c) 2026 CyberoniOntoni
 *
 * Permission to use, copy, modify, and/or distribute this
 * software for any purpose with or without fee is hereby granted,
 * provided that the above copyright notice and this permission
 * notice appear in all copies.
 */

#include "adb_client.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QStandardPaths>
#include <QProcessEnvironment>
#include <QDebug>
#include <QThread>

QString adb_client::find_adb(const QString& user_hint)
{
    if (!user_hint.isEmpty() && QFileInfo::exists(user_hint))
        return user_hint;

    const QString app_dir = QCoreApplication::applicationDirPath();
    const QStringList candidates = {
        app_dir + "/adb.exe",
        app_dir + "/platform-tools/adb.exe",
        app_dir + "/android/adb.exe",
        app_dir + "/adb",
        QStandardPaths::findExecutable("adb"),
        QStandardPaths::findExecutable("adb.exe"),
        QDir::homePath() + "/AppData/Local/Android/Sdk/platform-tools/adb.exe",
        "C:/platform-tools/adb.exe"
    };

    for (const QString& candidate : candidates)
    {
        if (!candidate.isEmpty() && QFileInfo::exists(candidate))
            return QDir::toNativeSeparators(candidate);
    }

    return QString();
}

QList<adb_client::device_info> adb_client::list_devices(const QString& adb_path, QString* error_msg)
{
    QList<device_info> result;
    if (adb_path.isEmpty() || !QFileInfo::exists(adb_path))
    {
        if (error_msg)
            *error_msg = QObject::tr("ADB executable not found. Please install Android Platform Tools or specify adb path.");
        return result;
    }

    QProcess proc;
    proc.start(adb_path, QStringList{"devices", "-l"});
    if (!proc.waitForFinished(4000))
    {
        if (error_msg)
            *error_msg = QObject::tr("ADB timed out while querying connected devices.");
        return result;
    }

    const QString output = QString::fromUtf8(proc.readAllStandardOutput());
    const QStringList lines = output.split(QRegExp("[\r\n]+"), QString::SkipEmptyParts);

    for (const QString& line : lines)
    {
        const QString trimmed = line.trimmed();
        if (trimmed.startsWith("List of devices") || trimmed.startsWith("*"))
            continue;

        const QStringList tokens = trimmed.split(QRegExp("\\s+"), QString::SkipEmptyParts);
        if (tokens.size() >= 2)
        {
            device_info dev;
            dev.serial = tokens[0];
            dev.status = tokens[1];

            for (const QString& token : tokens)
            {
                if (token.startsWith("model:"))
                    dev.model = token.mid(6);
            }
            result.append(dev);
        }
    }

    return result;
}

bool adb_client::check_device(const QString& adb_path, QString* error_msg, QString* chosen_serial)
{
    const QList<device_info> devices = list_devices(adb_path, error_msg);
    if (devices.isEmpty())
    {
        if (error_msg && error_msg->isEmpty())
            *error_msg = QObject::tr("No Android device detected over USB.\n"
                                     "1. Connect phone via USB\n"
                                     "2. Enable Developer Options -> USB Debugging\n"
                                     "3. Authorize computer on phone screen");
        return false;
    }

    for (const device_info& dev : devices)
    {
        if (dev.status == "device")
        {
            if (chosen_serial)
                *chosen_serial = dev.serial;
            return true;
        }
        if (dev.status == "unauthorized")
        {
            if (error_msg)
                *error_msg = QObject::tr("Android device '%1' is unauthorized.\n"
                                         "Please unlock phone and tap 'Allow USB debugging' (check 'Always allow').")
                                 .arg(dev.serial);
            return false;
        }
    }

    if (error_msg)
        *error_msg = QObject::tr("No ready Android devices found (all offline or unauthorized).");
    return false;
}

QString adb_client::get_device_abi(const QString& adb_path, const QString& serial)
{
    QStringList args;
    if (!serial.isEmpty())
        args << "-s" << serial;
    args << "shell" << "getprop" << "ro.product.cpu.abi";

    QProcess proc;
    proc.start(adb_path, args);
    if (proc.waitForFinished(3000))
        return QString::fromUtf8(proc.readAllStandardOutput()).trimmed();

    return "arm64-v8a"; // Default fallback for modern Android devices
}

QString adb_client::find_relay_binary(const QString& abi)
{
    const QString app_dir = QCoreApplication::applicationDirPath();
    const QString suffix = abi.contains("v7") ? "armv7" : "arm64";

    const QStringList candidates = {
        app_dir + "/android/st-relay-" + suffix,
        app_dir + "/st-relay-" + suffix,
        app_dir + "/st-relay",
        app_dir + "/../libexec/opentrack/st-relay-" + suffix,
        app_dir + "/../tracker-smoothtrack/android/st-relay-" + suffix
    };

    for (const QString& candidate : candidates)
    {
        if (QFileInfo::exists(candidate))
            return QDir::toNativeSeparators(candidate);
    }

    return QString();
}

bool adb_client::setup_reverse(const QString& adb_path, int host_port, int device_port,
                               const QString& serial, QString* error_msg)
{
    QStringList args;
    if (!serial.isEmpty())
        args << "-s" << serial;
    args << "reverse" << QString("tcp:%1").arg(device_port) << QString("tcp:%1").arg(host_port);

    QProcess proc;
    proc.start(adb_path, args);
    if (!proc.waitForFinished(4000) || proc.exitCode() != 0)
    {
        if (error_msg)
        {
            const QString err = QString::fromUtf8(proc.readAllStandardError()).trimmed();
            *error_msg = QObject::tr("adb reverse failed: %1").arg(err.isEmpty() ? "Unknown error" : err);
        }
        return false;
    }
    return true;
}

bool adb_client::remove_reverse(const QString& adb_path, int device_port, const QString& serial)
{
    QStringList args;
    if (!serial.isEmpty())
        args << "-s" << serial;
    args << "reverse" << "--remove" << QString("tcp:%1").arg(device_port);

    QProcess proc;
    proc.start(adb_path, args);
    return proc.waitForFinished(2000) && proc.exitCode() == 0;
}

adb_client::adb_client() = default;

adb_client::~adb_client()
{
    stop();
}

bool adb_client::start(const QString& adb_path, int udp_port, int tcp_port, QString* error_msg)
{
    stop();

    active_adb = adb_path;
    active_port = tcp_port;

    if (!check_device(active_adb, error_msg, &active_serial))
        return false;

    // 1. Setup reverse tunnel: Android tcp:tcp_port -> PC tcp:tcp_port
    if (!setup_reverse(active_adb, tcp_port, tcp_port, active_serial, error_msg))
        return false;

    // 2. Locate relay binary matching target ABI
    const QString abi = get_device_abi(active_adb, active_serial);
    const QString relay_bin = find_relay_binary(abi);

    if (relay_bin.isEmpty())
    {
        // Try to check if relay is already deployed on the device
        QStringList check_args;
        if (!active_serial.isEmpty())
            check_args << "-s" << active_serial;
        check_args << "shell" << "test" << "-x" << "/data/local/tmp/st-relay";
        QProcess check_proc;
        check_proc.start(active_adb, check_args);
        check_proc.waitForFinished(2000);

        if (check_proc.exitCode() != 0)
        {
            if (error_msg)
                *error_msg = QObject::tr("Relay binary (st-relay-%1) not found in OpenTrack directory.")
                                 .arg(abi.contains("v7") ? "armv7" : "arm64");
            remove_reverse(active_adb, active_port, active_serial);
            return false;
        }
    }
    else
    {
        // Push relay to /data/local/tmp/st-relay
        QStringList push_args;
        if (!active_serial.isEmpty())
            push_args << "-s" << active_serial;
        push_args << "push" << relay_bin << "/data/local/tmp/st-relay";

        QProcess push_proc;
        push_proc.start(active_adb, push_args);
        if (!push_proc.waitForFinished(5000) || push_proc.exitCode() != 0)
        {
            if (error_msg)
                *error_msg = QObject::tr("Failed to push relay binary to Android device.");
            remove_reverse(active_adb, active_port, active_serial);
            return false;
        }

        // Ensure executable permissions
        QStringList chmod_args;
        if (!active_serial.isEmpty())
            chmod_args << "-s" << active_serial;
        chmod_args << "shell" << "chmod" << "755" << "/data/local/tmp/st-relay";
        QProcess chmod_proc;
        chmod_proc.start(active_adb, chmod_args);
        chmod_proc.waitForFinished(2000);
    }

    // 3. Kill any previously hanging relay instances on the device
    QStringList kill_args;
    if (!active_serial.isEmpty())
        kill_args << "-s" << active_serial;
    kill_args << "shell" << "pkill" << "-f" << "st-relay";
    QProcess kill_proc;
    kill_proc.start(active_adb, kill_args);
    kill_proc.waitForFinished(2000);

    // Give socket time to unbind if killed
    QThread::msleep(100);

    // 4. Launch relay as a managed background process
    relay_proc = std::make_unique<QProcess>();
    QStringList run_args;
    if (!active_serial.isEmpty())
        run_args << "-s" << active_serial;
    run_args << "shell" << "/data/local/tmp/st-relay"
             << QString::number(udp_port) << QString::number(tcp_port);

    relay_proc->start(active_adb, run_args);
    if (!relay_proc->waitForStarted(3000))
    {
        if (error_msg)
            *error_msg = QObject::tr("Failed to launch relay on Android device.");
        relay_proc.reset();
        remove_reverse(active_adb, active_port, active_serial);
        return false;
    }

    return true;
}

void adb_client::stop()
{
    if (relay_proc)
    {
        if (relay_proc->state() != QProcess::NotRunning)
        {
            relay_proc->terminate();
            if (!relay_proc->waitForFinished(500))
                relay_proc->kill();
        }
        relay_proc.reset();
    }

    if (!active_adb.isEmpty() && QFileInfo::exists(active_adb))
    {
        if (!active_serial.isEmpty())
        {
            QStringList kill_args;
            kill_args << "-s" << active_serial << "shell" << "pkill" << "-f" << "st-relay";
            QProcess kill_proc;
            kill_proc.start(active_adb, kill_args);
            kill_proc.waitForFinished(1000);
        }

        if (active_port > 0)
            remove_reverse(active_adb, active_port, active_serial);
    }

    active_serial.clear();
    active_port = 0;
}

bool adb_client::is_running() const
{
    return relay_proc && relay_proc->state() == QProcess::Running;
}
