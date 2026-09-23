// Recorded server contract used only by isolated page/presentation unit tests.
const value=require('./business-options.json');
const catalog=require('../../miniprogram/utils/businessOptions');
catalog.install(value);
require('node:test').beforeEach(()=>catalog.install(value));
module.exports={value,catalog};
