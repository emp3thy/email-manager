/**
 * Token management for Microsoft Graph API authentication
 */
const fs = require('fs');
const https = require('https');
const querystring = require('querystring');
const config = require('../config');

// Global variable to store tokens
let cachedTokens = null;
// Guard against concurrent refresh calls — only one refresh at a time
let _refreshPromise = null;

/**
 * Loads authentication tokens from the token file
 * @returns {object|null} - The loaded tokens or null if not available
 */
function loadTokenCache() {
  try {
    const tokenPath = config.AUTH_CONFIG.tokenStorePath;
    console.error(`[DEBUG] Attempting to load tokens from: ${tokenPath}`);
    console.error(`[DEBUG] HOME directory: ${process.env.HOME}`);
    console.error(`[DEBUG] Full resolved path: ${tokenPath}`);
    
    // Log file existence and details
    if (!fs.existsSync(tokenPath)) {
      console.error('[DEBUG] Token file does not exist');
      return null;
    }
    
    const stats = fs.statSync(tokenPath);
    console.error(`[DEBUG] Token file stats:
      Size: ${stats.size} bytes
      Created: ${stats.birthtime}
      Modified: ${stats.mtime}`);
    
    const tokenData = fs.readFileSync(tokenPath, 'utf8');
    console.error('[DEBUG] Token file contents length:', tokenData.length);
    console.error('[DEBUG] Token file first 200 characters:', tokenData.slice(0, 200));
    
    try {
      const tokens = JSON.parse(tokenData);
      console.error('[DEBUG] Parsed tokens keys:', Object.keys(tokens));
      
      // Log each key's value to see what's present
      Object.keys(tokens).forEach(key => {
        console.error(`[DEBUG] ${key}: ${typeof tokens[key]}`);
      });
      
      // Check for access token presence
      if (!tokens.access_token) {
        console.error('[DEBUG] No access_token found in tokens');
        return null;
      }
      
      // Check token expiration
      const now = Date.now();
      const expiresAt = tokens.expires_at || 0;
      
      console.error(`[DEBUG] Current time: ${now}`);
      console.error(`[DEBUG] Token expires at: ${expiresAt}`);
      
      if (now > expiresAt) {
        console.error('[DEBUG] Token has expired, will attempt refresh');
        // Don't return null - let the caller use refreshAccessToken()
        // Store the expired tokens so refresh_token is available
        cachedTokens = tokens;
        return null;
      }
      
      // Update the cache
      cachedTokens = tokens;
      return tokens;
    } catch (parseError) {
      console.error('[DEBUG] Error parsing token JSON:', parseError);
      return null;
    }
  } catch (error) {
    console.error('[DEBUG] Error loading token cache:', error);
    return null;
  }
}

/**
 * Saves authentication tokens to the token file
 * @param {object} tokens - The tokens to save
 * @returns {boolean} - Whether the save was successful
 */
function saveTokenCache(tokens) {
  try {
    const tokenPath = config.AUTH_CONFIG.tokenStorePath;
    console.error(`Saving tokens to: ${tokenPath}`);
    
    fs.writeFileSync(tokenPath, JSON.stringify(tokens, null, 2));
    console.error('Tokens saved successfully');
    
    // Update the cache
    cachedTokens = tokens;
    return true;
  } catch (error) {
    console.error('Error saving token cache:', error);
    return false;
  }
}

/**
 * Refreshes the access token using the stored refresh token
 * @returns {Promise<string|null>} - The new access token or null if refresh failed
 */
async function refreshAccessToken() {
  // Deduplicate concurrent refresh calls — if a refresh is already in flight,
  // return the same promise so all callers get the same result and we don't
  // invalidate the refresh token by using it multiple times simultaneously.
  if (_refreshPromise) {
    console.error('[TOKEN-REFRESH] Refresh already in progress, waiting for existing refresh...');
    return _refreshPromise;
  }

  _refreshPromise = _doRefreshAccessToken();
  try {
    return await _refreshPromise;
  } finally {
    _refreshPromise = null;
  }
}

async function _doRefreshAccessToken() {
  // Load tokens from file to get refresh_token even if access_token expired
  const tokenPath = config.AUTH_CONFIG.tokenStorePath;
  let tokens = cachedTokens;

  if (!tokens || !tokens.refresh_token) {
    try {
      if (fs.existsSync(tokenPath)) {
        tokens = JSON.parse(fs.readFileSync(tokenPath, 'utf8'));
      }
    } catch (error) {
      console.error('[TOKEN-REFRESH] Error reading token file:', error.message);
      return null;
    }
  }

  if (!tokens || !tokens.refresh_token) {
    console.error('[TOKEN-REFRESH] No refresh token available');
    return null;
  }

  const clientId = config.AUTH_CONFIG.clientId;
  const clientSecret = config.AUTH_CONFIG.clientSecret;

  if (!clientId || !clientSecret) {
    console.error('[TOKEN-REFRESH] Missing client credentials. OUTLOOK_CLIENT_ID:', clientId ? 'set' : 'MISSING', 'OUTLOOK_CLIENT_SECRET:', clientSecret ? 'set' : 'MISSING');
    return null;
  }

  const tenantId = process.env.OUTLOOK_TENANT_ID || process.env.MS_TENANT_ID || 'consumers';
  const scopes = config.AUTH_CONFIG.scopes.concat(['offline_access']).join(' ');

  const postData = querystring.stringify({
    client_id: clientId,
    client_secret: clientSecret,
    refresh_token: tokens.refresh_token,
    grant_type: 'refresh_token',
    scope: scopes
  });

  return new Promise((resolve) => {
    const options = {
      hostname: 'login.microsoftonline.com',
      path: `/${tenantId}/oauth2/v2.0/token`,
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Content-Length': Buffer.byteLength(postData)
      }
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        if (res.statusCode >= 200 && res.statusCode < 300) {
          try {
            const tokenResponse = JSON.parse(data);
            const expiresAt = Date.now() + (tokenResponse.expires_in * 1000);

            const newTokens = {
              ...tokens,
              access_token: tokenResponse.access_token,
              refresh_token: tokenResponse.refresh_token || tokens.refresh_token,
              expires_at: expiresAt,
              expires_in: tokenResponse.expires_in,
              scope: tokenResponse.scope || tokens.scope
            };

            saveTokenCache(newTokens);
            console.error('[TOKEN-REFRESH] Token refreshed successfully, expires at:', new Date(expiresAt).toISOString());
            resolve(newTokens.access_token);
          } catch (error) {
            console.error('[TOKEN-REFRESH] Error parsing refresh response:', error.message);
            resolve(null);
          }
        } else {
          console.error(`[TOKEN-REFRESH] Refresh failed with status ${res.statusCode}: ${data}`);
          resolve(null);
        }
      });
    });

    req.on('error', (error) => {
      console.error('[TOKEN-REFRESH] Request error:', error.message);
      resolve(null);
    });

    req.write(postData);
    req.end();
  });
}

/**
 * Gets the current Graph API access token, loading from cache if necessary.
 * If the token is expired, attempts to refresh it automatically.
 * @returns {Promise<string|null>} - The access token or null if not available
 */
async function getAccessToken() {
  if (cachedTokens && cachedTokens.access_token && cachedTokens.expires_at && Date.now() < cachedTokens.expires_at) {
    return cachedTokens.access_token;
  }

  const tokens = loadTokenCache();
  if (tokens) {
    return tokens.access_token;
  }

  // Token expired or missing - try refresh
  console.error('[TOKEN] Access token expired or missing, attempting refresh...');
  const refreshedToken = await refreshAccessToken();
  return refreshedToken;
}

/**
 * Gets the current Flow API access token
 * @returns {string|null} - The Flow access token or null if not available
 */
function getFlowAccessToken() {
  const tokens = loadTokenCache();
  if (!tokens) return null;

  // Check if flow token exists and is not expired
  if (tokens.flow_access_token && tokens.flow_expires_at) {
    if (Date.now() < tokens.flow_expires_at) {
      return tokens.flow_access_token;
    }
  }

  return null;
}

/**
 * Saves Flow API tokens alongside existing Graph tokens
 * @param {object} flowTokens - The Flow tokens to save
 * @returns {boolean} - Whether the save was successful
 */
function saveFlowTokens(flowTokens) {
  try {
    const tokenPath = config.AUTH_CONFIG.tokenStorePath;

    // Load existing tokens
    let existingTokens = {};
    if (fs.existsSync(tokenPath)) {
      const tokenData = fs.readFileSync(tokenPath, 'utf8');
      existingTokens = JSON.parse(tokenData);
    }

    // Merge flow tokens
    const mergedTokens = {
      ...existingTokens,
      flow_access_token: flowTokens.access_token,
      flow_refresh_token: flowTokens.refresh_token,
      flow_expires_at: flowTokens.expires_at || (Date.now() + (flowTokens.expires_in || 3600) * 1000)
    };

    fs.writeFileSync(tokenPath, JSON.stringify(mergedTokens, null, 2));
    console.log('Flow tokens saved successfully');

    // Update cache
    cachedTokens = mergedTokens;
    return true;
  } catch (error) {
    console.error('Error saving Flow tokens:', error);
    return false;
  }
}

/**
 * Creates a test access token for use in test mode
 * @returns {object} - The test tokens
 */
function createTestTokens() {
  const testTokens = {
    access_token: "test_access_token_" + Date.now(),
    refresh_token: "test_refresh_token_" + Date.now(),
    expires_at: Date.now() + (3600 * 1000) // 1 hour
  };
  
  saveTokenCache(testTokens);
  return testTokens;
}

module.exports = {
  loadTokenCache,
  saveTokenCache,
  getAccessToken,
  refreshAccessToken,
  getFlowAccessToken,
  saveFlowTokens,
  createTestTokens
};
