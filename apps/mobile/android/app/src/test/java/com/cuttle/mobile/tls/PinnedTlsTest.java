package com.cuttle.mobile.tls;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.fail;

import java.io.IOException;
import java.util.concurrent.TimeUnit;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.mockwebserver.MockResponse;
import okhttp3.mockwebserver.MockWebServer;
import okhttp3.tls.HandshakeCertificates;
import okhttp3.tls.HeldCertificate;
import org.junit.Test;

/** Real local TLS handshakes against a self-signed server, as a Cuttle PC presents. */
public class PinnedTlsTest {
    private final HeldCertificate cert = new HeldCertificate.Builder()
        .commonName("localhost").addSubjectAlternativeName("some-other-name").build();
    private final HeldCertificate otherKey = new HeldCertificate.Builder().commonName("other").build();

    private interface PinsFor {
        PinnedTls.Pins at(int port);
    }

    private void call(PinsFor pins, boolean expectOk) throws Exception {
        HandshakeCertificates tls = new HandshakeCertificates.Builder().heldCertificate(cert).build();
        try (MockWebServer server = new MockWebServer()) {
            server.useHttps(tls.sslSocketFactory(), false);
            server.enqueue(new MockResponse().setBody("ok"));
            server.start();
            OkHttpClient client = PinnedTls.apply(new OkHttpClient.Builder(), pins.at(server.getPort()))
                .retryOnConnectionFailure(false).callTimeout(5, TimeUnit.SECONDS).build();
            try (Response response = client.newCall(new Request.Builder().url(server.url("/")).build()).execute()) {
                if (!expectOk) fail("Connection should have been refused");
                assertEquals("ok", response.body().string());
            } catch (IOException error) {
                if (expectOk) throw error;
            }
        }
    }

    private String mine() { return PinnedTls.fingerprint(cert.certificate()); }

    @Test public void pinnedSelfSignedKeyConnectsDespiteNameMismatch() throws Exception {
        call(port -> (h, p) -> p == port ? mine() : null, true);
    }

    @Test public void unpinnedSelfSignedIsRefused() throws Exception {
        call(port -> (h, p) -> null, false);
    }

    @Test public void differentPinnedKeyIsRefused() throws Exception {
        call(port -> (h, p) -> PinnedTls.fingerprint(otherKey.certificate()), false);
    }

    @Test public void pinForAnotherPortDoesNotApply() throws Exception {
        call(port -> (h, p) -> p == port + 1 ? mine() : null, false);
    }

    @Test public void probeReadsFingerprintAndReportsNotSystemTrusted() throws Exception {
        HandshakeCertificates tls = new HandshakeCertificates.Builder().heldCertificate(cert).build();
        try (MockWebServer server = new MockWebServer()) {
            server.useHttps(tls.sslSocketFactory(), false);
            server.start();
            PinnedTls.Probe probe = PinnedTls.probe(server.getHostName(), server.getPort(), 5000);
            assertEquals(mine(), probe.fingerprint);
            assertFalse(probe.systemTrusted);
        }
    }

    @Test public void fingerprintUsesColonSeparatedHex() {
        assertEquals(95, mine().length());
        assertEquals(mine().toUpperCase(), mine());
    }

    @Test public void hostKeysIgnoreCaseAndIpv6Brackets() {
        assertEquals("pc.local:8888", HostPins.key("PC.Local", 8888));
        assertEquals("::1:8888", HostPins.key("[::1]", 8888));
    }
}
