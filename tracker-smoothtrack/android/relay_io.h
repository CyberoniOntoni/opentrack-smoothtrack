/* Copyright (c) 2026 CyberoniOntoni
 *
 * Permission to use, copy, modify, and/or distribute this
 * software for any purpose with or without fee is hereby granted,
 * provided that the above copyright notice and this permission
 * notice appear in all copies.
 */

#ifndef RELAY_IO_H
#define RELAY_IO_H

#include <errno.h>
#include <stddef.h>
#include <unistd.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>

extern volatile int running;

/* 0 ok, -1 fail */
static int send_all(int fd, const void *buf, size_t n)
{
    const char *p = buf;
    size_t left = n;
    while (left)
    {
        ssize_t w = send(fd, p, left, 0);
        if (w < 0)
        {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (w == 0)
            return -1;
        p += (size_t)w;
        left -= (size_t)w;
    }
    return 0;
}

/* POSIX leaves socket state unspecified after a failed connect(); recreate fd. */
static int connect_retry(int *tcp_fd, const struct sockaddr_in *addr,
                         int attempts, int delay_us)
{
    for (int retry = 0; retry < attempts && running; ++retry)
    {
        if (*tcp_fd >= 0)
        {
            close(*tcp_fd);
            *tcp_fd = -1;
        }
        *tcp_fd = socket(AF_INET, SOCK_STREAM, 0);
        if (*tcp_fd < 0)
            return -1;
        int nodelay = 1;
        setsockopt(*tcp_fd, IPPROTO_TCP, TCP_NODELAY, &nodelay, sizeof(nodelay));
        if (connect(*tcp_fd, (const struct sockaddr *)addr, sizeof(*addr)) == 0)
            return 0;
        usleep(delay_us);
    }
    return -1;
}

#endif /* RELAY_IO_H */
