# SmoothTrack USB tracker (iOS via libusbmuxd + Android via ADB Relay)
option(OPENTRACK_HAS_USBMUXD "Build SmoothTrack with iOS libusbmuxd support" ON)

set(USBMUXD_FOUND FALSE)
set(USBMUXD_TARGET "")

if(OPENTRACK_HAS_USBMUXD)
    find_package(PkgConfig QUIET)
    if(PkgConfig_FOUND)
        pkg_check_modules(USBMUXD QUIET IMPORTED_TARGET libusbmuxd-2.0)
        if(TARGET PkgConfig::USBMUXD)
            set(USBMUXD_FOUND TRUE)
            set(USBMUXD_TARGET PkgConfig::USBMUXD)
        endif()
    endif()

    # vcpkg provides an unofficial CMake package on Windows
    if(NOT USBMUXD_FOUND)
        find_package(unofficial-libusbmuxd CONFIG QUIET)
        if(TARGET unofficial::libusbmuxd::libusbmuxd)
            set(USBMUXD_FOUND TRUE)
            set(USBMUXD_TARGET unofficial::libusbmuxd::libusbmuxd)
        endif()
    endif()

    if(NOT USBMUXD_FOUND)
        find_path(USBMUXD_INCLUDE_DIR NAMES usbmuxd.h)
        find_library(USBMUXD_LIBRARY NAMES usbmuxd usbmuxd-2.0 libusbmuxd)
        if(USBMUXD_INCLUDE_DIR AND USBMUXD_LIBRARY)
            set(USBMUXD_FOUND TRUE)
        endif()
    endif()
endif()

otr_module(tracker-smoothtrack)

if(WIN32)
    target_link_libraries(${self} PRIVATE ws2_32)
endif()

if(USBMUXD_FOUND)
    target_compile_definitions(${self} PRIVATE
        OPENTRACK_SMOOTHTRACK_HAVE_USBMUXD
        OPENTRACK_HAS_USBMUXD
        BUILD_USBMUXD
    )

    if(USBMUXD_TARGET)
        target_link_libraries(${self} PRIVATE ${USBMUXD_TARGET})
    else()
        target_include_directories(${self} SYSTEM PRIVATE "${USBMUXD_INCLUDE_DIR}")
        target_link_libraries(${self} PRIVATE "${USBMUXD_LIBRARY}")
    endif()

    if(WIN32)
        # Ship runtime DLLs next to modules so QLibrary can load the tracker.
        # Without these, LoadLibrary fails and the input never appears in the UI.
        set(_usbmuxd_runtime_names
            usbmuxd-2.0.dll
            usbmuxd.dll
            libusbmuxd-2.0.dll
            libusbmuxd.dll
            plist-2.0.dll
            libplist-2.0.dll
            libplist.dll
            libimobiledevice-glue-1.0.dll
            imobiledevice-glue-1.0.dll
        )
        set(_search_dirs "")
        if(DEFINED ENV{VCPKG_INSTALLATION_ROOT})
            list(APPEND _search_dirs
                "$ENV{VCPKG_INSTALLATION_ROOT}/installed/x64-windows/bin"
                "$ENV{VCPKG_INSTALLATION_ROOT}/installed/x64-windows/debug/bin"
            )
        endif()
        foreach(_p IN LISTS CMAKE_PREFIX_PATH)
            list(APPEND _search_dirs "${_p}/bin" "${_p}/debug/bin")
        endforeach()
        if(USBMUXD_LIBRARY)
            get_filename_component(_libdir "${USBMUXD_LIBRARY}" DIRECTORY)
            list(APPEND _search_dirs "${_libdir}" "${_libdir}/../bin")
        endif()

        foreach(_dll_name IN LISTS _usbmuxd_runtime_names)
            find_file(_dll_path_${_dll_name}
                NAMES "${_dll_name}"
                PATHS ${_search_dirs}
                NO_DEFAULT_PATH
            )
            if(_dll_path_${_dll_name})
                message(STATUS "SmoothTrack: installing runtime ${_dll_name} from ${_dll_path_${_dll_name}}")
                install(FILES "${_dll_path_${_dll_name}}"
                        DESTINATION "${opentrack-libexec}"
                        PERMISSIONS ${opentrack-perms-exec})
            endif()
        endforeach()
    endif()
else()
    message(STATUS "libusbmuxd not found — tracker-smoothtrack will support Android USB only")
endif()

# Cross-compile Android relays into the build tree when NDK clang is available.
set(_st_ndk "$ENV{ANDROID_NDK_ROOT}")
if(NOT _st_ndk)
    set(_st_ndk "$ENV{ANDROID_NDK_HOME}")
endif()
if(NOT _st_ndk)
    set(_st_ndk "$ENV{ANDROID_NDK_LATEST_HOME}")
endif()

set(_st_relay_dir "${CMAKE_CURRENT_BINARY_DIR}/android")
file(MAKE_DIRECTORY "${_st_relay_dir}")

set(_st_clang64 "")
set(_st_clang32 "")
if(_st_ndk)
    file(GLOB _st_ndk_bins "${_st_ndk}/toolchains/llvm/prebuilt/*/bin")
    find_program(_st_clang64
        NAMES aarch64-linux-android24-clang aarch64-linux-android24-clang.cmd
        PATHS ${_st_ndk_bins}
        NO_DEFAULT_PATH
    )
    find_program(_st_clang32
        NAMES armv7a-linux-androideabi24-clang armv7a-linux-androideabi24-clang.cmd
        PATHS ${_st_ndk_bins}
        NO_DEFAULT_PATH
    )
endif()

if(_st_clang64 AND _st_clang32)
    set(_st_relay_src "${CMAKE_CURRENT_SOURCE_DIR}/android/relay.c")
    add_custom_command(
        OUTPUT "${_st_relay_dir}/st-relay-arm64"
        COMMAND "${_st_clang64}" -O2 -Wall -Wextra -static "${_st_relay_src}"
                -o "${_st_relay_dir}/st-relay-arm64"
        DEPENDS "${_st_relay_src}"
        COMMENT "Cross-compiling st-relay-arm64"
    )
    add_custom_command(
        OUTPUT "${_st_relay_dir}/st-relay-armv7"
        COMMAND "${_st_clang32}" -O2 -Wall -Wextra -static "${_st_relay_src}"
                -o "${_st_relay_dir}/st-relay-armv7"
        DEPENDS "${_st_relay_src}"
        COMMENT "Cross-compiling st-relay-armv7"
    )
    add_custom_target(st-relay-android ALL
        DEPENDS
            "${_st_relay_dir}/st-relay-arm64"
            "${_st_relay_dir}/st-relay-armv7"
    )
    install(FILES
            "${_st_relay_dir}/st-relay-arm64"
            "${_st_relay_dir}/st-relay-armv7"
            DESTINATION "${opentrack-libexec}/android"
            PERMISSIONS ${opentrack-perms-exec})
else()
    message(WARNING "Android NDK clang not found — st-relay will not be built")
endif()

# Host test for relay I/O helpers. POSIX sockets only; not installed.
if(NOT WIN32)
    add_executable(test_relay_io "${CMAKE_SOURCE_DIR}/tests/test_relay_io.c")
    target_include_directories(test_relay_io PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/android")
endif()
