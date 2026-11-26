/**
 * Fluxus — Email Worker Cloudflare : transmet chaque newsletter reçue à l'instance Fluxus.
 *
 * Email Routing envoie ici tout le courrier du sous-domaine (règle « catch-all »). Le mail brut est
 * signé (HMAC-SHA256, secret partagé) et posté à `POST /api/inbound/email`.
 * - 202 : rangé dans Fluxus.
 * - 404 : adresse inconnue ou révoquée → mail rejeté (rien n'arrive nulle part).
 * - autre réponse, délai dépassé, mail trop gros : renvoi vers l'adresse de secours, rien n'est perdu.
 *
 * Variables : FLUXUS_URL (ex. https://fluxus.mondomaine.fr), FALLBACK_ADDRESS (adresse vérifiée
 * dans Email Routing) ; secret : FLUXUS_SECRET (Réglages → Newsletters → générer le secret).
 * Guide : docs/NEWSLETTERS.md.
 */

// Borne du serveur (`newsletters/service.MAX_EMAIL_BYTES`).
const MAX_BYTES = 15 * 1024 * 1024
const TIMEOUT_MS = 25_000

const hex = (buf) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('')

/** Même calcul que `newsletters/service.sign` : HMAC de `<ms>.<destinataire>.<sha256 du corps>`. */
export async function sign(secret, timestamp, recipient, body) {
  const digest = hex(await crypto.subtle.digest('SHA-256', body))
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'])
  const payload = `${timestamp}.${recipient.trim().toLowerCase()}.${digest}`
  return hex(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(payload)))
}

async function fallback(message, env, reason) {
  console.log(`Fluxus indisponible (${reason}) : renvoi vers l'adresse de secours`)
  if (env.FALLBACK_ADDRESS) {
    await message.forward(env.FALLBACK_ADDRESS)
  } else {
    // Sans adresse de secours : erreur, Cloudflare refuse le message (configurer FALLBACK_ADDRESS).
    throw new Error(`Fluxus indisponible (${reason}) et FALLBACK_ADDRESS absente`)
  }
}

export default {
  async email(message, env) {
    if (message.rawSize > MAX_BYTES) return fallback(message, env, 'message trop volumineux')
    const body = await new Response(message.raw).arrayBuffer()
    const timestamp = String(Date.now())
    let response
    try {
      response = await fetch(new URL('/api/inbound/email', env.FLUXUS_URL), {
        method: 'POST',
        headers: {
          'Content-Type': 'message/rfc822',
          'X-Fluxus-Timestamp': timestamp,
          'X-Fluxus-To': message.to,
          'X-Fluxus-Signature': await sign(env.FLUXUS_SECRET ?? '', timestamp, message.to, body),
        },
        body,
        signal: AbortSignal.timeout(TIMEOUT_MS),
      })
    } catch (err) {
      return fallback(message, env, String(err))
    }
    if (response.status === 404) {
      message.setReject('Adresse inconnue')
      return
    }
    if (!response.ok) return fallback(message, env, `HTTP ${response.status}`)
  },
}
