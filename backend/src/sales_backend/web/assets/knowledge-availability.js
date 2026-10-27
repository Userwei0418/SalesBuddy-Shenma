// Release-wide pause; this page never loads knowledge data or providers.
export const knowledgeAvailable = false;
export async function knowledgeUnavailable() {
  return {html: '<section class="card card-pad"><h1>知识库</h1><p role="status">知识库暂不能使用</p></section>'};
}
