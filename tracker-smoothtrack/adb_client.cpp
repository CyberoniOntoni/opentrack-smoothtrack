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
#include <QRegularExpression>

namespace {

#if QT_VERSION >= QT_VERSION_CHECK(5, 14, 0)
constexpr auto SplitSkipEmpty = Qt::SkipEmptyParts;
#else
constexpr auto SplitSkipEmpty = QString::SkipEmptyParts;
#endif

// Helper function to invoke adb commands with strict bounded timeout and guarantee no dangling processes
bool run_adb_cmd(const QString& adb_path, const QStringList& args, int timeout_ms,
                 QString* stdout_str = nullptr, QString* stderr_str = nullptr, int* exit_code = nullptr)
{
    if (adb_path.isEmpty() || !QFileInfo::exists(adb_path))
    {
        if (stderr_str)
            *stderr_str = "ADB executable not found";
        return false;
    }

    QProcess proc;
    proc.start(adb_path, args);

    if (!proc.waitForFinished(timeout_ms))
    {
        proc.kill();
        proc.waitForFinished(200);
        if (stderr_str)
            *stderr_str = "Process timed out";
        if (exit_code)
            *exit_code = -1;
        return false;
    }

    if (stdout_str)
        *stdout_str = QString::fromUtf8(proc.readAllStandardOutput());
    if (stderr_str)
        *stderr_str = QString::fromUtf8(proc.readAllStandardError());
    if (exit_code)
        *exit_code = proc.exitCode();

    return proc.exitCode() == 0;
}

} // anonymous namespace

QString adb_client::find_adb(const QString& user_hint)
{
    // 1. Check user-supplied path / hint
    if (!user_hint.trimmed().isEmpty())
    {
        const QString hint = user_hint.trimmed();
        if (QFileInfo::exists(hint) && !QFileInfo(hint).isDir())
            return QDir::toNativeSeparators(hint);

        // Check if user pointed to a directory containing adb
        const QStringList user_candidates = {
            hint + "/adb.exe",
            hint + "/adb",
            hint + "/platform-tools/adb.exe",
            hint + "/platform-tools/adb"
        };
        for (const QString& c : user_candidates)
        {
            if (QFileInfo::exists(c) && !QFileInfo(c).isDir())
                return QDir::toNativeSeparators(c);
        }
    }

    const QString app_dir = QCoreApplication::applicationDirPath();
    const QStringList candidates = {
        // app_dir root
        app_dir + "/adb.exe",
        app_dir + "/adb",
        // app_dir/modules
        app_dir + "/modules/adb.exe",
        app_dir + "/modules/adb",
        // app_dir/libexec/opentrack
        app_dir + "/libexec/opentrack/adb.exe",
        app_dir + "/libexec/opentrack/adb",
        app_dir + "/../libexec/opentrack/adb.exe",
        app_dir + "/../libexec/opentrack/adb",
        // app_dir/platform-tools
        app_dir + "/platform-tools/adb.exe",
        app_dir + "/platform-tools/adb",
        // app_dir/android
        app_dir + "/android/adb.exe",
        app_dir + "/android/adb",
        // system PATH
        QStandardPaths::findExecutable("adb"),
        QStandardPaths::findExecutable("adb.exe"),
        // Standard Android SDK installation locations
        QDir::homePath() + "/AppData/Local/Android/Sdk/platform-tools/adb.exe",
        "C:/platform-tools/adb.exe",
        "C:/Program Files (x86)/Android/android-sdk/platform-tools/adb.exe",
        QDir::homePath() + "/Android/Sdk/platform-tools/adb"
    };

    for (const QString& candidate : candidates)
    {
        if (!candidate.isEmpty() && QFileInfo::exists(candidate) && !QFileInfo(candidate).isDir())
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
        {
            if (!adb_path.isEmpty())
                *error_msg = QObject::tr("ADB executable not found at '%1'.\n"
                                         "Please verify the path in SmoothTrack settings or install Android Platform Tools.")
                                 .arg(adb_path);
            else
                *error_msg = QObject::tr("ADB executable not found.\n"
                                         "Please install Android Platform Tools, place adb in the OpenTrack directory or on system PATH, "
                                         "or specify its location in SmoothTrack settings.");
        }
        return result;
    }

    QString output, err_str;
    int exit_code = 0;
    if (!run_adb_cmd(adb_path, QStringList{"devices", "-l"}, DEFAULT_TIMEOUT_MS, &output, &err_str, &exit_code))
    {
        if (error_msg)
        {
            if (err_str == "Process timed out")
                *error_msg = QObject::tr("ADB timed out while querying connected devices.\n"
                                         "The ADB server may be unresponsive. Try running 'adb kill-server' in a terminal or reconnecting the USB cable.");
            else
                *error_msg = QObject::tr("ADB command failed (%1): %2")
                                 .arg(exit_code)
                                 .arg(err_str.trimmed().isEmpty() ? "Unknown error" : err_str.trimmed());
        }
        return result;
    }

    const QStringList lines = output.split(QRegularExpression("[\r\n]+"), SplitSkipEmpty);

    for (const QString& line : lines)
    {
        const QString trimmed = line.trimmed();
        if (trimmed.startsWith("List of devices") || trimmed.startsWith("*"))
            continue;

        const QStringList tokens = trimmed.split(QRegularExpression("\\s+"), SplitSkipEmpty);
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
            *error_msg = QObject::tr("No Android device detected over USB.\n\n"
                                     "Troubleshooting steps:\n"
                                     "1. Connect your Android phone to the PC via USB cable.\n"
                                     "2. Enable Developer Options on your phone:\n"
                                     "   Settings -> About Phone -> tap 'Build number' 7 times.\n"
                                     "3. Enable USB Debugging:\n"
                                     "   Settings -> Developer Options -> turn ON 'USB debugging'.\n"
                                     "4. Ensure USB connection mode is 'File Transfer' / 'MTP' (not 'Charge only').\n"
                                     "5. Check your phone screen for an authorization prompt.");
        return false;
    }

    // 1. First pass: prioritize any ready, authorized device
    for (const device_info& dev : devices)
    {
        if (dev.status == "device")
        {
            if (chosen_serial)
                *chosen_serial = dev.serial;
            return true;
        }
    }

    // 2. Second pass: surface specific diagnostics for unauthorized or offline devices
    for (const device_info& dev : devices)
    {
        if (dev.status == "unauthorized")
        {
            if (error_msg)
                *error_msg = QObject::tr("Android device '%1' is unauthorized.\n\n"
                                         "To authorize:\n"
                                         "1. Unlock your phone screen.\n"
                                         "2. Look for the prompt: 'Allow USB debugging?'.\n"
                                         "3. Check 'Always allow from this computer' and tap 'Allow'.\n"
                                         "4. If no prompt appears, reconnect the USB cable.")
                                 .arg(dev.serial);
            return false;
        }
        if (dev.status == "offline")
        {
            if (error_msg)
                *error_msg = QObject::tr("Android device '%1' is offline.\n\n"
                                         "1. Reconnect the USB cable.\n"
                                         "2. Toggle 'USB debugging' off and on in Developer Options.")
                                 .arg(dev.serial);
            return false;
        }
    }

    if (error_msg)
        *error_msg = QObject::tr("No ready Android devices found (all connected devices are offline or unauthorized).");
    return false;
}

QString adb_client::get_device_abi(const QString& adb_path, const QString& serial)
{
    QStringList args;
    if (!serial.isEmpty())
        args << "-s" << serial;
    args << "shell" << "getprop" << "ro.product.cpu.abi";

    QString output;
    if (run_adb_cmd(adb_path, args, 1500, &output))
    {
        const QString abi = output.trimmed();
        if (!abi.isEmpty())
            return abi;
    }

    return "arm64-v8a"; // Default fallback for modern Android devices
}

QString adb_client::find_relay_binary(const QString& abi)
{
    const QString app_dir = QCoreApplication::applicationDirPath();
    QString suffix = "arm64";
    if (abi.contains("v7") || abi.contains("armeabi"))
        suffix = "armv7";
    else if (abi.contains("x86_64"))
        suffix = "x86_64";
    else if (abi.contains("x86"))
        suffix = "x86";

    const QStringList candidates = {
        // 1. app_dir/modules/android (Windows standard installation layout)
        app_dir + "/modules/android/st-relay-" + suffix,
        app_dir + "/modules/android/st-relay",

        // 2. app_dir/android (Flat bundle / packaged layout)
        app_dir + "/android/st-relay-" + suffix,
        app_dir + "/android/st-relay",

        // 3. app_dir root
        app_dir + "/st-relay-" + suffix,
        app_dir + "/st-relay",

        // 4. app_dir/modules
        app_dir + "/modules/st-relay-" + suffix,
        app_dir + "/modules/st-relay",

        // 5. libexec/opentrack (Linux / FHS layouts)
        app_dir + "/../libexec/opentrack/android/st-relay-" + suffix,
        app_dir + "/libexec/opentrack/android/st-relay-" + suffix,
        app_dir + "/../libexec/opentrack/st-relay-" + suffix,
        app_dir + "/libexec/opentrack/st-relay-" + suffix,

        // 6. Source tree directories (development and build tree)
        app_dir + "/../tracker-smoothtrack/android/st-relay-" + suffix,
        app_dir + "/../../tracker-smoothtrack/android/st-relay-" + suffix,
        app_dir + "/tracker-smoothtrack/android/st-relay-" + suffix,
        app_dir + "/../tracker-smoothtrack/android/st-relay",
        app_dir + "/../../tracker-smoothtrack/android/st-relay",
        app_dir + "/tracker-smoothtrack/android/st-relay"
    };

    for (const QString& candidate : candidates)
    {
        if (!candidate.isEmpty() && QFileInfo::exists(candidate) && !QFileInfo(candidate).isDir())
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

    QString err_str;
    if (!run_adb_cmd(adb_path, args, DEFAULT_TIMEOUT_MS, nullptr, &err_str))
    {
        if (error_msg)
        {
            const QString err = err_str.trimmed();
            *error_msg = QObject::tr("Failed to setup ADB reverse port forwarding (tcp:%1 -> tcp:%2): %3\n"
                                     "Ensure the port is not already bound by another process.")
                             .arg(device_port)
                             .arg(host_port)
                             .arg(err.isEmpty() ? "Unknown error or timeout" : err);
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

    return run_adb_cmd(adb_path, args, 1500);
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
    {
        stop();
        return false;
    }

    // 1. Setup reverse tunnel: Android tcp:tcp_port -> PC tcp:tcp_port
    if (!setup_reverse(active_adb, tcp_port, tcp_port, active_serial, error_msg))
    {
        stop();
        return false;
    }

    // 2. Locate relay binary matching target ABI
    const QString abi = get_device_abi(active_adb, active_serial);
    const QString relay_bin = find_relay_binary(abi);
    const QString suffix = abi.contains("v7") || abi.contains("armeabi") ? "armv7" : "arm64";

    if (relay_bin.isEmpty())
    {
        // Check if relay is already deployed on the device
        QStringList check_args;
        if (!active_serial.isEmpty())
            check_args << "-s" << active_serial;
        check_args << "shell" << "test" << "-x" << "/data/local/tmp/st-relay";

        if (!run_adb_cmd(active_adb, check_args, 1500))
        {
            if (error_msg)
                *error_msg = QObject::tr("Relay binary (st-relay-%1) not found in OpenTrack directory.\n"
                                         "Please ensure OpenTrack modules/android files are intact.")
                                 .arg(suffix);
            stop();
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

        QString push_err;
        if (!run_adb_cmd(active_adb, push_args, DEFAULT_TIMEOUT_MS, nullptr, &push_err))
        {
            if (error_msg)
                *error_msg = QObject::tr("Failed to push relay binary to Android device: %1")
                                 .arg(push_err.trimmed().isEmpty() ? "Transfer error or timeout" : push_err.trimmed());
            stop();
            return false;
        }

        // Ensure executable permissions (chmod 755)
        QStringList chmod_args;
        if (!active_serial.isEmpty())
            chmod_args << "-s" << active_serial;
        chmod_args << "shell" << "chmod" << "755" << "/data/local/tmp/st-relay";
        run_adb_cmd(active_adb, chmod_args, 1500);
    }

    // 3. Kill any previously hanging relay instances on the device
    QStringList kill_args;
    if (!active_serial.isEmpty())
        kill_args << "-s" << active_serial;
    kill_args << "shell" << "pkill" << "-f" << "st-relay";
    run_adb_cmd(active_adb, kill_args, QUICK_TIMEOUT_MS);

    // Give socket a brief moment to unbind if killed
    QThread::msleep(100);

    // 4. Launch relay as a managed background process
    relay_proc = std::make_unique<QProcess>();
    QStringList run_args;
    if (!active_serial.isEmpty())
        run_args << "-s" << active_serial;
    run_args << "shell" << "/data/local/tmp/st-relay"
             << QString::number(udp_port) << QString::number(tcp_port);

    relay_proc->start(active_adb, run_args);
    if (!relay_proc->waitForStarted(DEFAULT_TIMEOUT_MS))
    {
        if (error_msg)
            *error_msg = QObject::tr("Failed to launch relay daemon on Android device.");
        relay_proc.reset();
        stop();
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
            {
                relay_proc->kill();
                relay_proc->waitForFinished(200);
            }
        }
        relay_proc.reset();
    }

    if (!active_adb.isEmpty() && QFileInfo::exists(active_adb))
    {
        QStringList kill_args;
        if (!active_serial.isEmpty())
            kill_args << "-s" << active_serial;
        kill_args << "shell" << "pkill" << "-f" << "st-relay";
        run_adb_cmd(active_adb, kill_args, QUICK_TIMEOUT_MS);

        if (active_port > 0)
            remove_reverse(active_adb, active_port, active_serial);
    }

    active_adb.clear();
    active_serial.clear();
    active_port = 0;
}

bool adb_client::is_running() const
{
    return relay_proc && relay_proc->state() == QProcess::Running;
}
