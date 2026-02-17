const API = {
  base: '',

  getToken() {
    return localStorage.getItem('token');
  },

  setAuth(data) {
    localStorage.setItem('token', data.access_token);
    localStorage.setItem('user_id', data.user_id);
    localStorage.setItem('user_name', data.name);
  },

  clearAuth() {
    localStorage.removeItem('token');
    localStorage.removeItem('user_id');
    localStorage.removeItem('user_name');
  },

  isAuthenticated() {
    return !!this.getToken();
  },

  async request(method, path, body = null) {
    const headers = { 'Content-Type': 'application/json' };
    const token = this.getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;

    const opts = { method, headers };
    if (body) opts.body = JSON.stringify(body);

    const res = await fetch(`${this.base}${path}`, opts);

    if (res.status === 401) {
      this.clearAuth();
      window.location.href = '/';
      throw new Error('Unauthorized');
    }

    if (res.status === 204) return null;

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Request failed');
    return data;
  },

  // Auth
  register(email, password, name) {
    return this.request('POST', '/auth/register', { email, password, name });
  },
  login(email, password) {
    return this.request('POST', '/auth/login', { email, password });
  },

  // Agents
  listAgents() {
    return this.request('GET', '/agents');
  },
  getAgent(id) {
    return this.request('GET', `/agents/${id}`);
  },
  createAgent(data) {
    return this.request('POST', '/agents', data);
  },
  updateAgent(id, data) {
    return this.request('PATCH', `/agents/${id}`, data);
  },
  deleteAgent(id) {
    return this.request('DELETE', `/agents/${id}`);
  },

  // Chat
  sendMessage(message, conversationId = null) {
    const body = { message };
    if (conversationId) body.conversation_id = conversationId;
    return this.request('POST', '/chat', body);
  },
  listConversations() {
    return this.request('GET', '/chat/conversations');
  },
  getConversation(id) {
    return this.request('GET', `/chat/conversations/${id}`);
  },
};
