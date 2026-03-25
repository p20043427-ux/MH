# OpenAPI.WorkFlow


<!-- page 1 -->
Zioyou OpenAPI UI Class
- 전자결재 양식 연동 API -
문서정보(Document Information)
⦁ 문서생성
⦁ 작성자 : 지오유 기업부설 연구소 신달수소장
⦁ 작성일자 : 2013년 03월 31일
⦁ 문서번호 : ZioYou-WorkFlowSyncForm-20130331001
⦁ 문서최종개정이력
⦁ 최종개정자 : 지오유 기업부설 연구소 박성규선임연구원
⦁ 최종개정일 : 2015년 05월 18일
⦁ 최종문서번호 : ZioYou-WorkFlowSyncForm-20150518003


<!-- page 2 -->
목차
⦁ 문서개정이력
⦁ 기본연동 규격
⦁ 정책 및 규정
⦁ 전자결재 양식 연동 API
⦁ 테스트 요청서 Sample


<!-- page 3 -->
문서개정이력 (최신순)
[ ZioYou-WorkFlowSyncForm-20150518003 ]
⦁ 문서번호 : ZioYou-WorkFlowSyncForm-20150518003
⦁ 개정일자 : 2015-05-18
⦁ 주요개정내용
⦁ ActProc() 함수의 리턴값 다변화
⦁ 기존에는, True/False만 반환했지만, 이제는 문서내용에 따라 변경하고자 하는
“결재라인번호”를 함께 반환할 수 있는 구조로 개선
⦁ 작업자는 결재문서의 내용에 따라 “결재라인번호”를 변경하는 것이 가능하게
됨
⦁ “결재라인번호”를 반환할 시, 현재 상신하는 문서의 결재라인이 반환된 “결재라
인번호”로 자동으로 변경되는 기능을 수행함.
⦁ “결재라인번호”를 반환 시, return true에 해당하는 것으로 이해하고, 결재상신
을 가능하도록 처리함
⦁ 개정자 : 지오유 박성규선임연구원
⦁ 최종감수자 : 지오유 신달수연구소장
[ ZioYou-WorkFlowSyncForm-20140809001 ]
⦁ 문서번호 : ZioYou-WorkFlowSyncForm-20140809001
⦁ 개정일자 : 2014-08-09
⦁ 주요개정내용
⦁ “연동C방식”에 대한 sample source code 기술
⦁ 위, 방식에 대한 화면 세로높이(Height) 값에 대한 자동 증감기능 버그수정으로
인한 호출 방법 및 함수명 업데이트
⦁ 개정자 : 지오유 박성규선임연구원
⦁ 최종감수자 : 지오유 신달수연구소장


<!-- page 4 -->
기본연동 규격
⦁ 연동포트
보안 및 범용성의 이유로 80포트만을 사용하여 연동 가능합니다.
더불어, 클라우드 서비스 이용시 방화벽 설정의 문제로 그 외의 다른 포트는 사용할 수
없습니다.
⦁ 전송방법
클라이언트는 XHR(XML Http Request) 및 http를 GET 방식 및 POST 방식을 통해 호출
합니다.
⦁ 호출자는 Http ASync 통신을 통해 결과값을 확인할 수 있습니다.
⦁ 성공 : String “success”
⦁ 실패 : String “failed:실패사유”
⦁ Http는 기본적으로 Async 통신을 하기 때문에, 성공이벤트를 수신을 통해 결과값을
받아야 합니다.
⦁ 호출자의 서버 페이지는 사전 등록되어야 합니다.
⦁ 본 OpenAPI는 “HTTP_REFFERER” 를 체크합니다. (즉, 미리 지정되거나 등록되지 않는 페
이지는 차단합니다. 어떠한 결과값도 받을 수 없습니다.)
⦁ 그러므로, 호출하는 서버 페이지의 URL은 반드시, 사전 등록되어져 있어야 합니다.
⦁ 지정한, 페이지에서의 호출만 받아들입니다.
⦁ 정확한 연동결과를 위해 외부시스템과 그룹웨어간의 “코드 및 데이터 매칭” 작업을 하는
경우가 발생합니다.
⦁ 본 API에서 제공하는 Parameter(파라메터) 전달 규칙들이 있습니다. (하단 페이지
참조)
⦁ 이종 시스템간 연동이기 때문에 그룹웨어에 사용자의 부주의로 인한 중복코드 입력
시 원하지 않은 데이터의 훼손이 일어날 수 있음을 주의하시기 바랍니다.
⦁
부주의로 인한 사용자 데이터의 훼손은 지오유에서 일체 책임을 지지 않습니다.


<!-- page 5 -->
⦁
정책 및 규정
지오유 그룹웨어 외부연동 API 사용에 대한 정책 및 규정입니다.
⦁ 외부연동의 이용목적
⦁ 외부연동 API의 개발목적은 이종 시스템간의 업무를 자동적/능동적으로 Sync 하는
것으로 타시스템과 그룹웨어와의 전자결재 양식 연동에 있습니다.
⦁ 외부연동 API는 지오유의 유료상품이며 계약하지 않고 API를 이용하여 어플리케이
션을 개발할 경우 민/형사상의 책임을 물을 수 있습니다.
※ 외부연동의 견적 및 문의는 고객센터 070-7094-6400번으로 연락주시기 바랍니다.
⦁ 외부연동의 사용방법
⦁ 전자결재 양식연동 API는 외부시스템간의 약속을 토대로, 전자결재 양식을 연동하
는 방식입니다.
⦁ 전자결재 양식연동으로 유입되는 정보가 없는 경우 상황에 따라 아무동작없이 끝나
거나, 빈페이지를 표시될 수 있습니다.
에러코드는 Sync통신으로 에러의 내용이 반환됩니다.
⦁ 단. 외부시스템과 전자결재 양식연동을 사용하기에 앞서 그룹웨어와 외부시스템간
에 사용자 계정 정보를 정확히 유입시켜야 합니다.
관리자 및 외부연동 시스템코드의 무분별한 유입으로 인한 데이터의 훼손은 지오유
에서 일체 책임을 지지 않습니다.
⦁ 외부연동 시 사용자데이터 보호 대책
지오유에서는 사용자의 데이터를 취급함에 있어 변조 또는 훼손되지 않도록 안전성 확
보를 위하여 다음과 같은 기술적/관리적 대책을 강구하고 있습니다.
⦁ 도메인 확인
그룹웨어에 등록된 도메인이 없을 경우(=계약정보) 무조건 에러코드를 리턴합니다.
⦁ 접속지(호출) 클라이언트의 IP 등록
원격지(호출)서버의 IP를 관리자가 등록 지정, IP와 도메인이 미리 등록되지 않을경
우 에러코드를 리턴 할 수 있습니다.


<!-- page 6 -->
전자결재 양식연동 API
⦁ 3가지 연동 방식
⦁ “A” 방식
[ 그림 : “A” 방식에 대한 처리 흐름도 ]
⦁ “A” 방식에 대한 설명
⦁ 기본적으로 전자결재 호출이, 연동하고자 하는 외부시스템에서 발생합니다.
⦁ 외부시스템(이하, ERP)에서 “전자결재 상신하겠습니까?” 라는 사용자 Interface
가 존재할 것으로 가정합니다.
⦁ ERP 시스템에서 호출이 될 때, 전달할 값들을, 넘겨줄 수도 있고, 그냥 키 값만
넘겨줄 수도 있습니다.
⦁ 위에서 받은, 데이터를 “전자결재시스템”에서 “임시보관함”에 넣어 놓습니다.
⦁ 사용자는, “임시보관함”에 있는 데이터를 근거로, 결재Process를 진행합니다.
⦁ “상신”, “승인”, “반려”일 때, ERP시스템에 특정신호를 호출합니다. (“양식관리”안
에 미리 지정된 호출URL임)
⦁ 아래, 그림에서 자세한 처리내용에 대해 설명합니다.


<!-- page 7 -->
[ 그림 : “A” 방식에 대한 처리순서별 처리내용 ]


<!-- page 8 -->
⦁ “B” 방식
[ 그림 : “B” 방식에 대한 처리 흐름도 ]
[ 그림 : “B” 방식에 대한 처리 순서별 내용 ]
⦁ “C” 방식


<!-- page 9 -->
[ 그림 : “C” 방식에 대한 처리 흐름도 ]
[ 그림 : “C” 방식에 대한 테이블 정의서 샘플 ]


<!-- page 10 -->
⦁ “C” 방식에 대한 사용법
⦁ “C” 방식 지원을 위한 현재, 전자결재 화면 구조
“결재라인 들어가는 영역”
“결재문서 Header 들어가는 영역”
“C 방식의 URL이 들어가는 iframe 영역”
⦁ iframe id값 : “AspFile”
⦁ iframe URL : 아래 2)번 “DB양식 사용시, 호출 URL 입력” 참조
⦁ cross domain 처리 방법 : 아래 “5)번” 설명 참조
⦁ iframe height(높이) 값 자동변경 방법 : 아래 “6)번” 설명 참조
⦁ 반드시 있어야 하는 javascript function : “ActProc()”
“첨부파일이 들어가는 영역”
⦁ 전자결재에서 양식을 신규로 만들 때, 아래와 같이 “DB양식사용”에 체크하셔야 합니
다.
[ 그림 : DB양식 사용시, 호출 URL 입력 ]
⦁ 위 항목에 대한 설명
⦁ “질문경로” : 초기 전자결재 상신화면에서 실행할 웹페이지 URL
⦁ “결과경로” : 상신한 페이지 및 View 페이지에 대한 URL
⦁ “화면높이” : 상신하는 페이지의 화면높이 (현재는, 높이를 계산해 자동으로 조정
합니다. 값을 입력안하셔도 됩니다.)
⦁ “승인명령” : 최종결재자가 최종 승인할 때 호출하는 웹페이지 URL
⦁ “반려명령” : 최종결재자가 최종 반려할 때 호출하는 웹페이지 URL
⦁ “삭제명령” : 전자결재 문서가 삭제할 때 호출할 는 웹페이지 URL
⦁ 파라메터(Parameter) 전달 값 정의
전달가능항목 값(Value) 비고

| ⦁ “C” 방식에 대한 사용법
⦁ “C” 방식 지원을 위한 현재, 전자결재 화면 구조
“결재라인 들어가는 영역”
“결재문서 Header 들어가는 영역”
“C 방식의 URL이 들어가는 iframe 영역”
⦁ iframe id값 : “AspFile”
⦁ iframe URL : 아래 2)번 “DB양식 사용시, 호출 URL 입력” 참조
⦁ cross domain 처리 방법 : 아래 “5)번” 설명 참조
⦁ iframe height(높이) 값 자동변경 방법 : 아래 “6)번” 설명 참조
⦁ 반드시 있어야 하는 javascript function : “ActProc()”
“첨부파일이 들어가는 영역”
⦁ 전자결재에서 양식을 신규로 만들 때, 아래와 같이 “DB양식사용”에 체크하셔야 합니
다.
[ 그림 : DB양식 사용시, 호출 URL 입력 ]
⦁ 위 항목에 대한 설명
⦁ “질문경로” : 초기 전자결재 상신화면에서 실행할 웹페이지 URL
⦁ “결과경로” : 상신한 페이지 및 View 페이지에 대한 URL
⦁ “화면높이” : 상신하는 페이지의 화면높이 (현재는, 높이를 계산해 자동으로 조정
합니다. 값을 입력안하셔도 됩니다.)
⦁ “승인명령” : 최종결재자가 최종 승인할 때 호출하는 웹페이지 URL
⦁ “반려명령” : 최종결재자가 최종 반려할 때 호출하는 웹페이지 URL
⦁ “삭제명령” : 전자결재 문서가 삭제할 때 호출할 는 웹페이지 URL
⦁ 파라메터(Parameter) 전달 값 정의 |  |  |
| --- | --- | --- |
| 전달가능항목 | 값(Value) | 비고 |


| “결재라인 들어가는 영역” |  |
| --- | --- |
| “결재문서 Header 들어가는 영역” |  |
| “C 방식의 URL이 들어가는 iframe 영역” ⦁ iframe id값 : “AspFile” ⦁ iframe URL : 아래 2)번 “DB양식 사용시, 호출 URL 입력” 참조 ⦁ cross domain 처리 방법 : 아래 “5)번” 설명 참조 ⦁ iframe height(높이) 값 자동변경 방법 : 아래 “6)번” 설명 참조 ⦁ 반드시 있어야 하는 javascript function : “ActProc()” |  |
| “첨부파일이 들어가는 영역” |  |



<!-- page 11 -->
회사코드 @corpcode ex) CorpCode
상신자 관련
상신자ID @uid ex) UserID
상신자명 @num ex) UserName
작성자명 @write_person ex) WriteUserName
부서 관련
상신자부서코드 @orgcode ex) 1
상신자부서명 @buseo ex) NowBuseo
상신자부서명 @orgname ex) 관리부
상신자부서Pass @orgpath ex) -1.0.1.3
외부코드 관련
사용자ERP번호 @fld1 ex) UserAltFld1
사용자ERP부서코드 @erporgcode ex) OrgPrevOrgCode
상신문서 관련
타이틀 @word_title ex) Title
문서발급번호 @dockey ex) DocKey, 기안-2013-0001
문서양식번호 @formno ex) FormNo
문서Key(Unique) @docid ex) TimeStamp
이전문서Key @olddocid ex) TimeStamp
승인구분 @approval ex) SignYN
DocNo @docno ex) 31425
이전DocNo @olddocno ex) 31424
⦁ 크로스 도메인(Cross Domain) 처리 방법
⦁ 만약, 전자결재시스템 URL의 도메인과 iframe에서 연동하는 페이지 URL의 도메
인이 상호 불일치 할 경우, document.domain으로 맞추어줘야 합니다.
⦁ 방법 페이지내 javascript에서 하단의 코드 추가
⦁ <script language=”javascript”>
⦁ window.onload = function(){
⦁ var b = window.location.hostname.split(“.”);
⦁ document.domain = b.slice(1).toString().replace(/,/g,’.’); //일치시킬도메인;
⦁ }
⦁ </script>
⦁ iframe 높이를 자동으로 변경하는 방법
⦁ 페이내 하단의 코드를 삽입하세요.
⦁ <iframe id="frmResize" src="about:blank" style="display:none;"></iframe>
⦁ window.onload = function() {
⦁ var iHeight = (document.body.scrollHeight>document.body.offsetHeight) ?
document.body.scrollHeight : document.body.offsetHeight;
⦁ if(iHeight!=0){
⦁ iHeight=iHeight+16;
⦁ var hostname="";
⦁ if (hostname=="") {
⦁ var b=window.location.hostname.split(".");

| 회사코드 | @corpcode | ex) CorpCode |
| --- | --- | --- |
| 상신자 관련 |  |  |
| 상신자ID | @uid | ex) UserID |
| 상신자명 | @num | ex) UserName |
| 작성자명 | @write_person | ex) WriteUserName |
| 부서 관련 |  |  |
| 상신자부서코드 | @orgcode | ex) 1 |
| 상신자부서명 | @buseo | ex) NowBuseo |
| 상신자부서명 | @orgname | ex) 관리부 |
| 상신자부서Pass | @orgpath | ex) -1.0.1.3 |
| 외부코드 관련 |  |  |
| 사용자ERP번호 | @fld1 | ex) UserAltFld1 |
| 사용자ERP부서코드 | @erporgcode | ex) OrgPrevOrgCode |
| 상신문서 관련 |  |  |
| 타이틀 | @word_title | ex) Title |
| 문서발급번호 | @dockey | ex) DocKey, 기안-2013-0001 |
| 문서양식번호 | @formno | ex) FormNo |
| 문서Key(Unique) | @docid | ex) TimeStamp |
| 이전문서Key | @olddocid | ex) TimeStamp |
| 승인구분 | @approval | ex) SignYN |
| DocNo | @docno | ex) 31425 |
| 이전DocNo | @olddocno | ex) 31424 |
| ⦁ 크로스 도메인(Cross Domain) 처리 방법 ⦁ 만약, 전자결재시스템 URL의 도메인과 iframe에서 연동하는 페이지 URL의 도메 인이 상호 불일치 할 경우, document.domain으로 맞추어줘야 합니다. ⦁ 방법 페이지내 javascript에서 하단의 코드 추가 ⦁ <script language=”javascript”> ⦁ window.onload = function(){ ⦁ var b = window.location.hostname.split(“.”); ⦁ document.domain = b.slice(1).toString().replace(/,/g,’.’); //일치시킬도메인; ⦁ } ⦁ </script> ⦁ iframe 높이를 자동으로 변경하는 방법 ⦁ 페이내 하단의 코드를 삽입하세요. ⦁ <iframe id="frmResize" src="about:blank" style="display:none;"></iframe> ⦁ window.onload = function() { ⦁ var iHeight = (document.body.scrollHeight>document.body.offsetHeight) ? document.body.scrollHeight : document.body.offsetHeight; ⦁ if(iHeight!=0){ ⦁ iHeight=iHeight+16; ⦁ var hostname=""; ⦁ if (hostname=="") { ⦁ var b=window.location.hostname.split("."); |  |  |



<!-- page 12 -->
⦁ hostname="gw."+b.slice(1).toString().replace(/,/g,'.');
⦁ }
⦁ hostname="ekp.tae-hyung.co.kr";
document.getElementById('frmResize').src="http://"+hostname+"/includes/i
frameResize?iframeHeight="+iHeight;
⦁ }
⦁ }
⦁ 반드시 있어야 하는 ”function ActProc(){“
⦁ 전자결재에서 “상신하기” 버튼 클릭 시, “ActProc()” 이라는 함수를 호출합니다.
⦁ 이 함수의 용도는 개발자의 의도대로 구현하시면 됩니다. 주로, 결재내용을 내부
저장할 때 사용합니다.
⦁ 이미, 이 함수는 만들어져 있어야 합니다.
⦁ return true; return false; 반환합니다.
⦁ 다른 값을 반환할 수 도 있는데, 그럴 경우, return true로 간주하여 “결재문서상
신” 처리를 진행합니다.
⦁ 리턴값(return value) 정의표
반환값 데이터형식 Sample
True boolean return true;
False boolean return false;
LineNo Json String var str = ‘{“LineNo”:14}’;
Json Object return str;
or
var obj = JSON.parse(str);
return obj;
LineStr Json String var str = '{"LineStr":"gildong|홍길동|2|개발팀|연구원
Json Object |102|K|Y|YYN|||15|?owner|대표자|1|회사|대표|49
|K|Y|YYN|||2|?"}';
return str;
or
var obj = JSON.parse(str);
return obj;
⦁ “LineNo”값에 대한 세부설명
⦁ 결재문서의 내용에 따라, 결재라인을 미리 지정된 값으로 변경하고 싶을 때
사용한다. 주로, “전결규정”의 경우에 해당한다고 볼 수 있다. 예를들어, 30만
원 이하의 결재내용일 경우, 미리 지정한 결재라인을 태우고 싶을 때 주로 사
용된다.
⦁ “결재라인”은 전자결재->환경설정->결재라인관리에서 미리 만들어줘야 한
다.

| 반환값 | 데이터형식 | Sample |  |
| --- | --- | --- | --- |
| True | boolean | return true; |  |
| False | boolean | return false; |  |
| LineNo | Json String Json Object | var str = ‘{“LineNo”:14}’; return str; or var obj = JSON.parse(str); return obj; |  |
| LineStr | Json String Json Object | var str = '{"LineStr":"gildong|홍길동|2|개발팀| |102|K|Y|YYN|||15|?owner|대표자|1|회사|대표|4 |K|Y|YYN|||2|?"}'; return str; or var obj = JSON.parse(str); return obj; | 연구원 9 |



<!-- page 13 -->
⦁ 상신자가, 이미 지정한 결재라인을 의미없게 만들어 버린다. 반환된
“LineNo”에 해당하는 결재라인으로 강제적 변경되기 때문이다. 물론, 상신자
에게 본 내용은 인지할 수 있도록 화면표시 및 Notification 된다.
⦁ 반환값이 “LineNo”를 표시한 Json String으로 올 경우, return true 기능을
내포하고 있다. 즉, 상신자가 정상적으로 문서를 “상신처리” 완료한 상태가
된다.
⦁ “LineStr”값에 대한 세부설명
⦁ 결재문서의 내용에 따라, 결재자 정보를 미리 지정된 값으로 변경하고 싶을
때 사용한다.
⦁ 해당 값의 구조(각 결재자별 정보구성)는 아래와 같다.
gildong|홍길동|2|개발팀|연구원|102|K|Y|YYN|||15|?
⦁ “gildong” : 지오유 그룹웨어에 설정되어 있는 사용자 ID
⦁ “홍길동” : 지오유 그룹웨어에 설정되어 있으며, 결재라인정보에 표시될
성명
⦁ “2” : 지오유 그룹웨어에 설정되어 있는 소속 부서코드
⦁ “개발팀” : 지오유 그룹웨어에 설정되어 있는 소속 부서명
⦁ “연구원” : 지오유 그룹웨어에 설정되어 있는 직위명
⦁ “102” : 지오유 그룹웨어에 설정되어 있는 직위코드
⦁ “K” : Sign구분 [“K”-결재, “P”-전결, “X”-결재/전결, “H”-협조, “W”-합의,
“F”-승인]
⦁ “Y” : 바로도착(동시동보) 여부 [“Y”:바로도착, “N”:바로도착아님]
⦁ “YYN” : 권한정보
(첫번째) “Y” : 바로도착 여부 [“Y”:바로도착, “N”:바로도착아님]
(두번째) “N” : 결재라인 수정권한 여부 [“Y”:수정가능, “N”:수정불가능]
(세번째) “N” : 내용 수정권한 여부 [“Y”:수정가능, “N”:수정불가능]
⦁ “” : 사용안함.
⦁ “” : 사용안함
⦁ “15” : 직위정렬순서 (지오유 그룹웨어 관리자 > 사용자계정관리 > 직위/
직책코드 에서 설정되어 있는 정렬순서값)
⦁ “?” : 결재자별 구분자값. 추가적으로 다음결재자가 있을 경우 반복연결
문자입니다
예시) "gildong|홍길동|2|개발팀|연구원|102|K|Y|YYN|||15|?owner|대표자|1|회
사|대표|49|K|Y|YYN|||2|?"
⦁ 각 결재자 정보의 마지막은 “?”구분자를 반드시 넣어야 하며(결재자가 한명
이더라도 입력해야 함), 여러명의 결재자 정보를 넘길 경우 넘겨진 순서대로
결재라인이 지정된다. 위 2)번 예시를 기준으로 하면 “홍길동 -> 대표자”로
결재진행
⦁ 상신자가 지정한 결재라인이 있더라도 해당 “LineStr”에 넘어온 정보로 강
제로 변경 처리된다. 물론, 상신자에게 변경된 결재라인을 인지할 수 있도록


<!-- page 14 -->
화면표시 및 Notification 된다.
⦁ 반환값이 “LineStr”을 포함한 Json String으로 넘어 올 경우, 지오유 그룹웨
어에서는 return true 기능을 내포하고 있다고 간주하여 정상적인 상신과정
을 진행한다.
⦁ 부서코드/명 확인방법 : 관리자 > 사용자계정관리 > 부서관리
⦁ 직위코드/명, 직위정렬순서 확인방법 : 관리자 > 사용자계정관리 > 직위/직
책관리
⦁ 위, 정해진 반환값이 아닐 경우, return false;에 해당하는 경우로 간주해, “전자결
재상신” 기능은 더 진행할 수 없다. (상신완료 페이지로 넘어가지 않음)
⦁ 결재라인번호(LineNo)를 알 수 있는 방법
[ 전자결재->환경설정->결재라인관리 화면에서 확인 ]
⦁ 연동하는 웹페이지 구현 시, 주의 할 내용
⦁ Cross 도메인 문제가 있다면, document.domain의 값을 상위 도메인과 일치시켜
야 한다.
⦁ 반드시, 필요한 Javascript function이 존재해야 한다. 함수명 = “ActProc()” 이다.
⦁ 위 함수는 return true, return false 또는 json값을 반환해야 한다.
⦁ 만약, ActProc() 함수 및 반환값이 없다면, return false;에 해당하는것으로 간주하
고 상신작업을 더 이상 진행할 수 없다. (상신완료 페이지로 넘어가지 않음)
⦁ 다음 페이지(Next Pages)를 두어 작업 가능
⦁ 주로, “조회 및 검색” 페이지를 제작할 경우, 사용된다.
⦁ 사용자는, 특정 조회 및 검색을 통해 특정 값을 선택하게 하고 싶을 때, 다음 페
이지를 통해, 그런 기능을 구현할 수 있다.
⦁ 추가로, 내부DB(Database)에 저장하는 페이지를 두고 싶을 때 사용된다.
⦁ 위와 같이 다음 페이지는 여러 개가 만들어 질 수 있다. 이럴 경우, 매우 중요한
내용은 각 페이지마다, “ActProc()” 함수를 두어야 하며, 각 페이지 입력 요소에
따라 Alert처리해서 사용자에게 다음 페이지로 가도록 유도해야 한다.


<!-- page 15 -->
⦁ 전자결재 인쇄화면 제작에 관하여
⦁ 별도의, 레포트 디자이너를 클라우드 고객에게는 제공하지 않습니다.
⦁ 그러나, 이미 위 DB양식 페이지를 제작하여 연동하였을 경우, 이미 인쇄양식에
서는 자동으로 그 연동페이지를 화면에 표시합니다.
⦁ 이때, 인쇄물 Heder 부분이라든지? Footer 부분, 그리고 결재라인이 표시되는
영역은 이미 제공하는 여러가지 형태를 Mapping할 수 있도록 제공해 드리고
있습니다.
⦁ 그나마도, 원하는 인쇄양식이 없을 경우, 추가로 요청하시면, 제작해 드립니다.
(유료로 진행될 수 있습니다.)
⦁ 위 화면에서 “(A)영역”이 기본으로 시스템에서 제공하는 출력양식입니다.
⦁ “(B)영역” 처럼, 고객사의 요청에 의해, 인쇄양식이 제작된다면 위와 같이 리스트
에 표시됩니다.
⦁ 대부분은, 결재 본문 내용이 바뀌는 것이지? 그 양식을 둘러싸고 있는 테두리
(Header+결재라인영역+Footer)들은 통일되게 운용하는 것이 일반적입니다.
⦁ sample code
⦁ 아래 페이지부터, sample page 및 code들을 참고하시기 바랍니다.


<!-- page 16 -->


<!-- page 17 -->
[ 상신URL sample page : ex] request.aspx?~~ ]
<html>
<head>
<link href='./includes/style_erp.css' rel='stylesheet' type='text/css'>
<script src="./frmResize.js"></script>
<iframe id="frmResize" src="about:blank" style="display:none;"></iframe>
<script language="javascript">
document.domain="tae-hyung.co.kr";
function ActProc() {
alert("전표를 선택해주세요.");
return false;
}
</script>
</head>
<body style="margin:0">
<table border="0" width="100%" cellspacing="0" cellpadding="0">
<tr height="30">
<td class="table_title" align='center'>전표번호</td>
<td class="table_title" align='center'>기표일</td>
<td class="table_title">거래처</td>
<td class="table_title">계정과목</td>
<td class="table_title" align='center'>금액</td>
<td class="table_title_rno">적요</td>
</tr>
<tr height='80'><td colspan='10' align='center'>자료가 존재하지 않습니다. (전자결재를
상신하실 수 없습니다.)</td></tr>
</table>
</body>
</html>
[ ./frmResize.js ]
window.onload = function() {
var iHeight = (document.body.scrollHeight>document.body.offsetHeight) ?
document.body.scrollHeight : document.body.offsetHeight;
if(iHeight!=0){
iHeight=iHeight+16;


<!-- page 18 -->
var hostname="";
if (hostname=="") {
var b=window.location.hostname.split(".");
hostname="gw."+b.slice(1).toString().replace(/,/g,'.');
}
hostname="ekp.tae-hyung.co.kr";
document.getElementById('frmResize').src="http://"+hostname+"/includes/iframeR
esize?iframeHeight="+iHeight;
}
}


<!-- page 19 -->
⦁ 테스트 요청서 Sample #1


<!-- page 20 -->


<!-- page 21 -->


<!-- page 22 -->