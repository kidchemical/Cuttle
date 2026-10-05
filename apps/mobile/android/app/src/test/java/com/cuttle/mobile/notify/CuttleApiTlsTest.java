package com.cuttle.mobile.notify;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.fail;

import java.io.IOException;
import java.util.concurrent.TimeUnit;
import javax.net.ssl.SSLHandshakeException;
import javax.net.ssl.SSLPeerUnverifiedException;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.tls.HandshakeCertificates;
import okhttp3.tls.HeldCertificate;
import org.junit.Test;

/** Real local TLS handshakes against the production API client's validation. */
public class CuttleApiTlsTest {
    private final HeldCertificate ca = new HeldCertificate.Builder()
        .commonName("Test CA").certificateAuthority(0).build();

    private HeldCertificate leaf(String hostname, boolean expired) {
        HeldCertificate.Builder builder = new HeldCertificate.Builder()
            .commonName(hostname).addSubjectAlternativeName(hostname).signedBy(ca);
        if (expired) {
            long now = System.currentTimeMillis();
            builder.validityInterval(now - 172800000L, now - 86400000L);
        }
        return builder.build();
    }

    private OkHttpClient client(boolean trustTestCa) {
        OkHttpClient.Builder builder = CuttleApi.client().newBuilder()
            .retryOnConnectionFailure(false)
            .callTimeout(5, TimeUnit.SECONDS);
        if (trustTestCa) {
            // Supply a test CA only; preserve the production hostname verifier.
            HandshakeCertificates trust = new HandshakeCertificates.Builder()
                .addTrustedCertificate(ca.certificate()).build();
            builder.sslSocketFactory(trust.sslSocketFactory(), trust.trustManager());
        }
        return builder.build();
    }

    private void request(HeldCertificate certificate, boolean trustTestCa,
                         Class<? extends IOException> expectedFailure) throws Exception {
        HandshakeCertificates serverTls = new HandshakeCertificates.Builder()
            .heldCertificate(certificate, ca.certificate()).build();
        try (MockWebServer server = new MockWebServer()) {
            server.useHttps(serverTls.sslSocketFactory(), false);
            server.enqueue(new MockResponse().setBody("ok"));
            server.start();
            Request request = new Request.Builder().url(server.url("/api/lan-ping")).build();
            try (Response response = client(trustTestCa).newCall(request).execute()) {
                if (expectedFailure != null) {
                    fail("Invalid TLS connection was accepted");
                }
                assertEquals(200, response.code());
                assertEquals("ok", response.body().string());
            } catch (IOException error) {
                if (expectedFailure == null || !expectedFailure.isInstance(error)) {
                    throw error;
                }
            }
        }
    }

    @Test public void untrustedCertificateIsRejected() throws Exception {
        request(leaf("localhost", false), false, SSLHandshakeException.class);
    }

    @Test public void trustedMatchingCertificateIsAccepted() throws Exception {
        request(leaf("localhost", false), true, null);
    }

    @Test public void trustedCertificateForAnotherHostIsRejected() throws Exception {
        request(leaf("another.example", false), true, SSLPeerUnverifiedException.class);
    }

    @Test public void expiredTrustedCertificateIsRejected() throws Exception {
        request(leaf("localhost", true), true, SSLHandshakeException.class);
    }
}
