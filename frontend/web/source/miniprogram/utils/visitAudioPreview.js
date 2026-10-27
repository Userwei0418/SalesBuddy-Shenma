// A preview ends on a complete MPEG Layer III frame, never an arbitrary byte.
// Incomplete/unknown containers are skipped; the full recording still proceeds.
function firstPreview(buffers, minimumSeconds = 15) {
  const size = buffers.reduce((sum, part) => sum + part.byteLength, 0);
  if (!size || size > 2 * 1024 * 1024) return null;
  const bytes = new Uint8Array(size); let offset = 0;
  buffers.forEach(part => {bytes.set(new Uint8Array(part), offset);offset += part.byteLength;});
  let cursor = 0, seconds = 0;
  if (bytes[0] === 73 && bytes[1] === 68 && bytes[2] === 51) {
    if (size < 10 || [...bytes.slice(6,10)].some(value => value > 127)) return null;
    cursor = 10 + ((bytes[6]<<21)|(bytes[7]<<14)|(bytes[8]<<7)|bytes[9]);
  }
  while (cursor + 4 <= size) {
    const a=bytes[cursor],b=bytes[cursor+1],c=bytes[cursor+2];
    const version=(b>>3)&3,layer=(b>>1)&3,rate=(c>>2)&3,index=c>>4;
    if(a!==255||(b&224)!==224||version===1||layer!==1||rate===3||index===0||index===15)return null;
    const sample=[44100,48000,32000][rate]/(version===3?1:version===2?2:4);
    const bitrate=(version===3?[0,32,40,48,56,64,80,96,112,128,160,192,224,256,320]:[0,8,16,24,32,40,48,56,64,80,96,112,128,144,160])[index];
    const length=Math.floor((version===3?144000:72000)*bitrate/sample)+((c>>1)&1);
    if(cursor+length>size)return null;
    cursor+=length;seconds+=(version===3?1152:576)/sample;
    if(seconds>=minimumSeconds)return {data:bytes.buffer.slice(0,cursor),seconds};
  }
  return null;
}
module.exports={firstPreview};
