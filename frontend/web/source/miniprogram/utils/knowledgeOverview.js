// Count exactly the same current-authorized published catalogue as the Mini
// directory. Internal imported rows are not automatically customer-shareable.
const SOURCE='salesbuddy.customer_distribution';
function normalizeKnowledgeOverview(directory) {
  const available=directory && directory.source===SOURCE && Array.isArray(directory.items)
    && Number.isSafeInteger(directory.total) && directory.total>=0;
  return {status:available?'available':'unconfigured',count:available?directory.total:null,
    value:available?String(directory.total):'—',source:available?SOURCE:'',sourceLabel:'可对客资料',
    observedAt:directory?.observed_at||'',error:''};
}
function knowledgeOverviewError(error='') {return {...normalizeKnowledgeOverview(null),status:'unavailable',error:String(error||'')};}
module.exports={normalizeKnowledgeOverview,knowledgeOverviewError};
