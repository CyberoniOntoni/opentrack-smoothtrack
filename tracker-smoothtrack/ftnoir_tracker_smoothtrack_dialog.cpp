/* Copyright (c) 2025-2026 CyberoniOntoni
 *
 * Permission to use, copy, modify, and/or distribute this
 * software for any purpose with or without fee is hereby granted,
 * provided that the above copyright notice and this permission
 * notice appear in all copies.
 */

#include "ftnoir_tracker_smoothtrack.h"
#include "api/plugin-api.hpp"

#include <QStandardItemModel>

dialog_smoothtrack::dialog_smoothtrack()
{
    ui.setupUi(this);

    connect(ui.buttonBox, SIGNAL(accepted()), this, SLOT(doOK()));
    connect(ui.buttonBox, SIGNAL(rejected()), this, SLOT(doCancel()));

    tie_setting(s.platform, ui.comboPlatform);
    tie_setting(s.port, ui.spinPortNumber);
    tie_setting(s.android_port, ui.spinAndroidPortNumber);
    tie_setting(s.adb_path, ui.lineAdbPath);

    tie_setting(s.add_yaw, ui.add_yaw);
    tie_setting(s.add_pitch, ui.add_pitch);
    tie_setting(s.add_roll, ui.add_roll);

    connect(ui.comboPlatform, SIGNAL(currentIndexChanged(int)),
            ui.stackedSettings, SLOT(setCurrentIndex(int)));
    ui.stackedSettings->setCurrentIndex(int(s.platform));

#if !defined(OPENTRACK_SMOOTHTRACK_HAVE_USBMUXD)
    ui.comboPlatform->setItemText(PLATFORM_IOS, tr("iOS (Apple USB via usbmuxd) [Not compiled]"));
    if (auto* model = qobject_cast<QStandardItemModel*>(ui.comboPlatform->model()))
    {
        if (auto* item = model->item(PLATFORM_IOS))
            item->setEnabled(false);
    }
    if (int(s.platform) == PLATFORM_IOS)
    {
        ui.comboPlatform->setCurrentIndex(PLATFORM_ANDROID);
    }
    ui.pageIOS->setEnabled(false);
#endif
}

void dialog_smoothtrack::doOK()
{
    s.b->save();
    close();
}

void dialog_smoothtrack::doCancel()
{
    s.b->reload();
    close();
}
