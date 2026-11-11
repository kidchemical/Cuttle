package com.cuttle.mobile.tls;

import java.net.InetSocketAddress;
import java.net.Socket;
import java.security.GeneralSecurityException;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.cert.Certificate;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import javax.net.ssl.HostnameVerifier;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLEngine;
import javax.net.ssl.SSLSession;
import javax.net.ssl.SSLSocket;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509ExtendedTrustManager;
import javax.net.ssl.X509TrustManager;
import okhttp3.OkHttpClient;

/**
 * System certificate validation, plus the one key the user trusted for a given
 * host:port. A Cuttle PC has a self-signed certificate (no public CA signs for a
 * private IP), so the phone trusts its public key the way the desktop client does.
 * The fingerprint is SHA-256 of the public key: `python -m api.tls_cert fingerprint`.
 */
public final class PinnedTls {
    /** The trusted fingerprint for host:port, or null. */
    public interface Pins {
        String fingerprint(String host, int port);
    }

    /** What a server presented. Nothing but the TLS handshake is exchanged. */
    public static final class Probe {
        public final boolean systemTrusted;
        public final String fingerprint;

        Probe(boolean systemTrusted, String fingerprint) {
            this.systemTrusted = systemTrusted;
            this.fingerprint = fingerprint;
        }
    }

    private PinnedTls() {}

    public static OkHttpClient.Builder apply(OkHttpClient.Builder builder, Pins pins) {
        try {
            X509TrustManager trust = new PinTrustManager(systemTrust(), pins);
            SSLContext context = SSLContext.getInstance("TLS");
            context.init(null, new TrustManager[] {trust}, null);
            return builder.sslSocketFactory(context.getSocketFactory(), trust)
                .hostnameVerifier(verifier(pins));
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("TLS setup failed", e);
        }
    }

    public static String fingerprint(Certificate certificate) {
        try {
            byte[] hash = MessageDigest.getInstance("SHA-256").digest(certificate.getPublicKey().getEncoded());
            StringBuilder out = new StringBuilder(hash.length * 3);
            for (byte b : hash) {
                if (out.length() > 0) out.append(':');
                out.append(String.format("%02X", b & 0xff));
            }
            return out.toString();
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    /** Reads the certificate a server presents without trusting it for anything. */
    public static Probe probe(String host, int port, int timeoutMs) throws Exception {
        X509TrustManager system = systemTrust();
        X509Certificate[][] seen = new X509Certificate[1][];
        boolean[] trusted = new boolean[1];
        X509TrustManager recorder = new X509TrustManager() {
            @Override public void checkClientTrusted(X509Certificate[] chain, String authType) {}
            @Override public void checkServerTrusted(X509Certificate[] chain, String authType) {
                seen[0] = chain;
                try {
                    system.checkServerTrusted(chain, authType);
                    trusted[0] = true;
                } catch (CertificateException ignored) {
                    trusted[0] = false;
                }
            }
            @Override public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
        };
        SSLContext context = SSLContext.getInstance("TLS");
        context.init(null, new TrustManager[] {recorder}, null);
        try (Socket raw = new Socket()) {
            raw.connect(new InetSocketAddress(host, port), timeoutMs);
            raw.setSoTimeout(timeoutMs);
            try (SSLSocket socket = (SSLSocket) context.getSocketFactory().createSocket(raw, host, port, false)) {
                socket.startHandshake();
                boolean hostMatches = new OkHttpClient().hostnameVerifier().verify(host, socket.getSession());
                return new Probe(trusted[0] && hostMatches, fingerprint(seen[0][0]));
            }
        }
    }

    private static X509TrustManager systemTrust() throws GeneralSecurityException {
        TrustManagerFactory factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        factory.init((KeyStore) null);
        for (TrustManager manager : factory.getTrustManagers()) {
            if (manager instanceof X509TrustManager) return (X509TrustManager) manager;
        }
        throw new GeneralSecurityException("No system trust manager");
    }

    private static HostnameVerifier verifier(Pins pins) {
        HostnameVerifier standard = new OkHttpClient().hostnameVerifier();
        return (host, session) -> matchesPin(pins, host, session) || standard.verify(host, session);
    }

    private static boolean matchesPin(Pins pins, String host, SSLSession session) {
        try {
            String pinned = pins.fingerprint(host, session.getPeerPort());
            return pinned != null && pinned.equals(fingerprint(session.getPeerCertificates()[0]));
        } catch (Exception e) {
            return false;
        }
    }

    /** Extended so the check knows which host:port it is judging. */
    private static final class PinTrustManager extends X509ExtendedTrustManager {
        private final X509TrustManager system;
        private final Pins pins;

        PinTrustManager(X509TrustManager system, Pins pins) {
            this.system = system;
            this.pins = pins;
        }

        private void check(X509Certificate[] chain, String authType, SSLSession session) throws CertificateException {
            try {
                system.checkServerTrusted(chain, authType);
            } catch (CertificateException error) {
                String pinned = session == null ? null : pins.fingerprint(session.getPeerHost(), session.getPeerPort());
                if (pinned == null || chain.length == 0 || !pinned.equals(fingerprint(chain[0]))) throw error;
            }
        }

        @Override public void checkServerTrusted(X509Certificate[] chain, String authType, Socket socket)
                throws CertificateException {
            check(chain, authType, socket instanceof SSLSocket ? ((SSLSocket) socket).getHandshakeSession() : null);
        }

        @Override public void checkServerTrusted(X509Certificate[] chain, String authType, SSLEngine engine)
                throws CertificateException {
            check(chain, authType, engine.getHandshakeSession());
        }

        @Override public void checkServerTrusted(X509Certificate[] chain, String authType)
                throws CertificateException {
            check(chain, authType, null);
        }

        @Override public void checkClientTrusted(X509Certificate[] chain, String authType, Socket socket)
                throws CertificateException {
            throw new CertificateException("Client certificates are not used");
        }

        @Override public void checkClientTrusted(X509Certificate[] chain, String authType, SSLEngine engine)
                throws CertificateException {
            throw new CertificateException("Client certificates are not used");
        }

        @Override public void checkClientTrusted(X509Certificate[] chain, String authType)
                throws CertificateException {
            throw new CertificateException("Client certificates are not used");
        }

        @Override public X509Certificate[] getAcceptedIssuers() {
            return system.getAcceptedIssuers();
        }
    }
}
