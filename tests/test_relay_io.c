/* Host test for relay send_all / connect_retry (POSIX sockets). */

#include "relay_io.h"

#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>

volatile int running = 1;

static int fail(const char *msg)
{
    perror(msg);
    return 1;
}

static int make_loopback_listener(int *out_fd, struct sockaddr_in *out_addr)
{
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    if (srv < 0)
        return -1;

    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    memset(out_addr, 0, sizeof(*out_addr));
    out_addr->sin_family = AF_INET;
    out_addr->sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    out_addr->sin_port = 0;

    if (bind(srv, (struct sockaddr *)out_addr, sizeof(*out_addr)) < 0)
    {
        close(srv);
        return -1;
    }

    socklen_t alen = sizeof(*out_addr);
    if (getsockname(srv, (struct sockaddr *)out_addr, &alen) < 0)
    {
        close(srv);
        return -1;
    }

    if (listen(srv, 1) < 0)
    {
        close(srv);
        return -1;
    }

    *out_fd = srv;
    return 0;
}

int main(void)
{
    /* send_all of 48 bytes to a listening socket that reads 48 */
    int srv = -1;
    struct sockaddr_in addr;
    if (make_loopback_listener(&srv, &addr) != 0)
        return fail("listen");

    int tcp_fd = -1;
    if (connect_retry(&tcp_fd, &addr, 50, 1000) != 0)
    {
        fprintf(stderr, "connect_retry to listener failed\n");
        close(srv);
        return 1;
    }

    int acc = accept(srv, NULL, NULL);
    if (acc < 0)
        return fail("accept");

    unsigned char payload[48];
    unsigned char got[48];
    for (int i = 0; i < 48; ++i)
        payload[i] = (unsigned char)(i + 1);

    if (send_all(tcp_fd, payload, sizeof(payload)) != 0)
    {
        fprintf(stderr, "send_all failed\n");
        return 1;
    }

    size_t nread = 0;
    while (nread < sizeof(got))
    {
        ssize_t r = recv(acc, got + nread, sizeof(got) - nread, 0);
        if (r <= 0)
        {
            fprintf(stderr, "recv failed after %zu bytes\n", nread);
            return 1;
        }
        nread += (size_t)r;
    }

    if (memcmp(payload, got, sizeof(payload)) != 0)
    {
        fprintf(stderr, "payload mismatch\n");
        return 1;
    }

    close(acc);
    close(tcp_fd);
    close(srv);

    /* connect_retry must socket() a new fd after failed connect() */
    int probe = socket(AF_INET, SOCK_STREAM, 0);
    if (probe < 0)
        return fail("socket probe");
    struct sockaddr_in ephemeral;
    memset(&ephemeral, 0, sizeof(ephemeral));
    ephemeral.sin_family = AF_INET;
    ephemeral.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    ephemeral.sin_port = 0;
    if (bind(probe, (struct sockaddr *)&ephemeral, sizeof(ephemeral)) < 0)
        return fail("bind probe");
    socklen_t plen = sizeof(ephemeral);
    if (getsockname(probe, (struct sockaddr *)&ephemeral, &plen) < 0)
        return fail("getsockname probe");
    close(probe); /* port is bound to nothing: connect should fail */

    int orig = socket(AF_INET, SOCK_STREAM, 0);
    if (orig < 0)
        return fail("socket orig");
    int keepalive = 1;
    if (setsockopt(orig, SOL_SOCKET, SO_KEEPALIVE, &keepalive, sizeof(keepalive)) != 0)
        return fail("SO_KEEPALIVE");

    int fd = orig;
    if (connect_retry(&fd, &ephemeral, 2, 1000) == 0)
    {
        fprintf(stderr, "connect_retry unexpectedly succeeded\n");
        if (fd >= 0)
            close(fd);
        return 1;
    }
    if (fd < 0)
    {
        fprintf(stderr, "connect_retry left tcp fd invalid\n");
        return 1;
    }

    keepalive = 1;
    socklen_t klen = sizeof(keepalive);
    if (getsockopt(fd, SOL_SOCKET, SO_KEEPALIVE, &keepalive, &klen) != 0)
        return fail("getsockopt SO_KEEPALIVE");
    if (keepalive != 0)
    {
        fprintf(stderr, "connect_retry reused the failed-connect fd\n");
        close(fd);
        return 1;
    }
    close(fd);

    return 0;
}
