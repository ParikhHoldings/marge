export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  const { email, product } = req.body;
  if (!email) return res.status(400).json({ error: 'Email required' });

  const BEEHIIV_KEY = process.env.BEEHIIV_API_KEY;
  const PUB_ID = process.env.BEEHIIV_PUB_ID || 'pub_0aa91871-b0db-4985-836e-d530b93727d9';
  const PRODUCT_FIELD_ID = process.env.BEEHIIV_PRODUCT_FIELD_ID || 'fca5d640-2a0a-4bfb-9945-0d98e931232f';

  const slug = product?.toLowerCase().replace(/ /g, '-').replace(/[^a-z0-9-]/g, '');

  const response = await fetch(`https://api.beehiiv.com/v2/publications/${PUB_ID}/subscriptions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${BEEHIIV_KEY}` },
    body: JSON.stringify({
      email,
      reactivate_existing: true,
      send_welcome_email: true,
      tags: [product, 'waitlist', 'marge'],
      custom_fields: [{ id: PRODUCT_FIELD_ID, value: product }],
      utm_source: slug,
      utm_medium: 'waitlist',
      utm_campaign: 'coming-soon'
    })
  });

  const data = await response.json();

  if (response.ok) {
    const subId = data?.data?.id;
    if (subId) {
      await fetch(`https://api.beehiiv.com/v2/publications/${PUB_ID}/subscriptions/${subId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${BEEHIIV_KEY}` },
        body: JSON.stringify({
          custom_fields: [{ id: PRODUCT_FIELD_ID, value: product }]
        })
      });
    }
    return res.status(200).json({ success: true });
  } else {
    return res.status(400).json({ error: data });
  }
}
