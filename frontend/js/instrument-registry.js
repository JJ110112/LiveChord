/**
 * InstrumentRegistry — 前端樂器註冊表
 * 新增樂器只需 register() 一行，不需改 player.js 核心邏輯。
 */
window.InstrumentRegistry = {
  _instruments: {},

  register(id, instance) {
    this._instruments[id] = instance;
  },

  get(id) {
    return this._instruments[id] || null;
  },

  list() {
    return Object.keys(this._instruments);
  },

  isStringInstrument(id) {
    return this._instruments[id] instanceof StringInstrument;
  },
};
