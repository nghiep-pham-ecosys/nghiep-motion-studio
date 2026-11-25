# Bình phản ứng khuấy trộn liên tục (CSTR) trong process-sim-lab

33 scenes, 2460 words (about 16.4 min at 150 words per minute).

## Mở đầu

**Narrator** (intro): Xin chào các bạn. Hôm nay chúng ta mở một khối nhỏ trong process-sim-lab. Đó là một bộ mô phỏng quá trình mã nguồn mở, viết bằng Python. Khối này là bình phản ứng khuấy trộn liên tục. Tên tiếng Anh viết tắt là xê ét tê e, đọc theo từng chữ cái. Hầu hết kỹ sư hoá học đều học thiết bị này đầu tiên. Mã nguồn của nó đủ ngắn để đọc hết trong một lần. Cuối video, bạn sẽ hiểu mô hình và các con số trong bài kiểm thử. Bạn cũng sẽ biết cách chọn thể tích bình cho một độ chuyển hoá mong muốn. Video này dành cho sinh viên kỹ thuật hoá học và người mới học mô phỏng. Bạn chỉ cần biết một chút đại số và một chút Python.

**Guest** (ask-look): Trước khi vào toán, anh cho tôi hỏi một chút. Bình phản ứng khuấy trộn trông như thế nào?

## Chiếc bình

**Narrator** (tank): Hãy hình dung một chiếc bình có cánh khuấy. Dòng chất lỏng chảy vào với lưu lượng ổn định. Cùng lưu lượng đó chảy ra ở cửa ra. Trong bình, phản ứng biến A thành B khi chất lỏng chảy qua. Nước, hay dung môi nào khác, chỉ đi qua mà không đổi. Cánh khuấy mạnh đến mức mọi điểm trong bình đều giống nhau. Đó là ý chính của thiết bị này: khuấy trộn hoàn toàn. Hình bên cạnh vẽ một bình như vậy. Mũi tên bên trái là dòng vào, mũi tên bên phải là dòng ra.

**Narrator** (mixing): Khuấy trộn hoàn toàn dẫn tới một hệ quả bất ngờ. Chất lỏng chảy ra chính là chất lỏng đang ở trong bình. Vì vậy nồng độ ở cửa ra cũng là nồng độ ở mọi nơi trong bình. Một giọt nguyên liệu mới bị pha loãng ngay vào khối chất lỏng. Trong khối đó, phần lớn chất A đã phản ứng rồi. Bạn hãy nhớ hình ảnh này. Nó giải thích gần như mọi điều phía sau. Nói cách khác, bình không nhớ chất lỏng vào từ lúc nào. Mọi phân tử trong bình đều thấy cùng một nồng độ thấp.

**Guest** (ask-assume): Mô hình trong process-sim-lab còn giả thiết gì khác nữa không anh?

## Các giả thiết

**Narrator** (assumptions): Có, mô hình dùng năm giả thiết. Bình chạy ở trạng thái dừng, nên không có gì thay đổi theo thời gian. Bình đẳng nhiệt, nên nhiệt độ không đổi. Lưu lượng thể tích Q cũng không đổi. Đó là vì khối lượng riêng của chất lỏng không đổi. Chỉ có một phản ứng, từ A sang B, và nó không chạy ngược lại. Mỗi mol A phản ứng tạo ra đúng một mol B. Và phản ứng là bậc một. Tốc độ phản ứng bằng k nhân với nồng độ của chất A.

## Thời gian lưu

**Narrator** (tau): Con số đầu tiên mà mã tính ra là thời gian lưu, ký hiệu là tau. Tau bằng thể tích V chia cho lưu lượng thể tích Q. Đơn vị của tau là giây. Hãy hiểu tau là thời gian trung bình một giọt nằm trong bình. Bình càng lớn, hoặc dòng càng chậm, thì phản ứng càng có nhiều thời gian. Ví dụ, lấy một bình hai mét khối. Lưu lượng là không phẩy không không bốn mét khối trên giây. Khi đó tau bằng năm trăm giây, tức là hơn tám phút một chút.

## Cân bằng mol

**Narrator** (balance): Bây giờ ta viết cân bằng mol cho chất A, tức chất phản ứng. Ở trạng thái dừng, lượng vào bằng lượng ra cộng lượng đã phản ứng. Viết bằng ký hiệu, ép A không bằng ép A cộng k nhân xê A nhân thể tích. Ép A không là lưu lượng mol của chất A trong dòng vào. Ép A là lưu lượng mol của chất A đi ra. Vì bình khuấy đều, xê A chỉ là ép A chia cho lưu lượng. Thay vào phương trình cân bằng, ta giải được ngay trong một dòng. Ép A bằng ép A không chia cho một cộng k tau. Bạn thấy đấy, chỉ cần một phép chia là có lời giải. Không cần lặp, không cần giải phương trình vi phân.

**Guest** (ask-conversion): Vậy rốt cuộc có bao nhiêu phần A thật sự chuyển thành B?

**Narrator** (conversion): Đó là độ chuyển hoá, ký hiệu là ích. Nó là phần của lượng A đi vào đã tham gia phản ứng. Từ phương trình cân bằng, ích bằng k tau chia cho một cộng k tau. Tích k tau có một cái tên riêng, gọi là số Damkohler. Con số này so tốc độ phản ứng với thời gian chất lỏng nằm trong bình.

**Narrator** (damkohler): Bạn để ý rằng độ chuyển hoá chỉ phụ thuộc vào số Damkohler. Số Damkohler bằng một cho đúng một nửa độ chuyển hoá. Số Damkohler bằng năm cho năm phần sáu. Hai bình có thể khác nhau rất nhiều về kích thước và tốc độ. Chúng vẫn cho cùng kết quả nếu số Damkohler bằng nhau. Vì thế mã trả về số Damkohler bên cạnh độ chuyển hoá. Nó cho bạn biết mình đang đứng ở đâu trên đường cong. Khi số Damkohler nhỏ hơn một, độ chuyển hoá chưa tới một nửa. Phần lớn chất A rời bình trước khi kịp phản ứng. Khi số Damkohler lớn hơn một nhiều, gần như toàn bộ A đã phản ứng.

## Mã nguồn

**Narrator** (code-func): Đây là phần cốt lõi của hàm isothermal cstr first order. Hàm tính tau bằng thể tích chia cho lưu lượng thể tích. Số Damkohler bằng hằng số tốc độ nhân với tau. Độ chuyển hoá bằng số Damkohler chia cho một cộng chính nó. Tiếp theo, lượng đã phản ứng bằng lưu lượng A vào nhân độ chuyển hoá. Mã trừ lượng đó khỏi chất A, tức chất phản ứng. Mã cộng đúng lượng đó vào chất B, tức sản phẩm. Dòng ra giữ nguyên nhiệt độ và áp suất của dòng vào. Hàm trả về một đối tượng kết quả nhỏ gồm bốn trường. Đó là dòng ra, độ chuyển hoá, thời gian lưu và số Damkohler.

**Guest** (ask-bad): Nếu tôi lỡ truyền vào thể tích bằng không thì sao anh?

**Narrator** (checks): Hàm sẽ từ chối ngay, trước khi tính bất cứ thứ gì. Hằng số tốc độ phải lớn hơn hoặc bằng không. Thể tích và lưu lượng thể tích đều phải lớn hơn không. Chất phản ứng phải có trong dòng vào. Chất phản ứng và sản phẩm phải là hai chất khác nhau. Mỗi đầu vào sai đều gây ra lỗi value error, kèm thông báo rõ ràng. Có bài kiểm thử cho từng trường hợp này. Kiểm tra đầu vào sớm như vậy giúp lỗi lộ ra ngay. Bạn không phải đi tìm một con số vô lý ở cuối sơ đồ công nghệ.

## Ví dụ có số

**Narrator** (example): Bây giờ là ví dụ trong tài liệu và trong bài kiểm thử. Dòng vào có chất A với lưu lượng mười mol trên giây. Dòng vào cũng có năm mươi mol trên giây nước. Nhiệt độ là ba trăm ba mươi độ Kelvin. Hằng số tốc độ là không phẩy không một trên giây. Thể tích bình là hai mét khối. Lưu lượng thể tích là không phẩy không không bốn mét khối trên giây.

**Guest** (ask-guess): Để tôi đoán thời gian lưu nhé. Hai chia cho không phẩy không không bốn. Có phải là năm trăm giây không?

**Narrator** (example-numbers): Chính xác, năm trăm giây. Nhân với hằng số tốc độ, ta được số Damkohler bằng năm. Độ chuyển hoá là năm phần sáu, khoảng tám mươi ba phẩy ba phần trăm. Bài kiểm thử so giá trị này tới mười hai chữ số thập phân. Nó cũng so lưu lượng của A và B ở dòng ra theo cách đó.

**Narrator** (outlet): Vậy cái gì đi ra khỏi bình? Lưu lượng A giảm từ mười xuống khoảng một phẩy sáu bảy mol trên giây. B tăng từ không lên khoảng tám phẩy ba ba. Nước đi qua không đổi, vẫn là năm mươi. Tổng lưu lượng vẫn là sáu mươi mol trên giây. Đó là vì một mol A tạo ra đúng một mol B. Không có gì tự sinh ra hay mất đi. Bạn có thể tự kiểm tra bằng tay. Mười trừ một phẩy sáu bảy bằng tám phẩy ba ba. Đó đúng là lượng B tạo ra.

## Các bài kiểm thử

**Narrator** (tests): Các bài kiểm thử rất ngắn và đáng đọc. Bài thứ nhất so độ chuyển hoá với giá trị giải tích. Bài thứ hai kiểm tra rằng số mol được bảo toàn. Nó cũng kiểm tra nhiệt độ và áp suất giữ nguyên. Bài thứ ba cho hằng số tốc độ bằng không. Khi đó độ chuyển hoá bằng không, và không có chút B nào được tạo ra. Bài thứ tư kiểm tra rằng bình lớn hơn cho độ chuyển hoá cao hơn. Nhưng độ chuyển hoá không bao giờ đạt đủ một trăm phần trăm. Bài cuối đưa vào các đầu vào sai, và lần nào cũng phải báo lỗi. Đọc các bài kiểm thử là cách nhanh nhất để hiểu một hàm làm gì. Mỗi bài kiểm thử là một câu khẳng định có thể chạy được.

## Chọn kích thước bình

**Guest** (ask-design): Trong nhà máy thật, tôi bắt đầu từ mục tiêu. Bình phải lớn cỡ nào để đạt chín mươi phần trăm độ chuyển hoá?

**Narrator** (design): Ta đảo ngược công thức. Ích bằng k tau chia cho một cộng k tau. Vậy k tau bằng ích chia cho một trừ ích. Với chín mươi phần trăm, đó là không phẩy chín chia không phẩy một. Vậy số Damkohler phải bằng chín. Hằng số tốc độ là không phẩy không một trên giây. Vậy tau phải bằng chín trăm giây. Lưu lượng vẫn là không phẩy không không bốn mét khối trên giây. Vậy thể tích bình là ba phẩy sáu mét khối.

**Narrator** (diminishing): Bây giờ đẩy mục tiêu lên chín mươi chín phần trăm. Số Damkohler nhảy lên chín mươi chín. Thể tích nhảy lên ba mươi chín phẩy sáu mét khối. Tức là gấp mười một lần thể tích, chỉ để thêm chín phần trăm. Vài phần trăm cuối cùng rất đắt với một bình khuấy trộn. Đây là bài học quan trọng khi thiết kế. Đừng đặt mục tiêu độ chuyển hoá cao hơn mức thật sự cần.

## CSTR và PFR

**Guest** (ask-why): Tại sao bình khuấy trộn lại tốn kém như vậy ở đoạn cuối?

**Narrator** (curve): Là do khuấy trộn hoàn toàn. Cả bình nằm ở nồng độ của dòng ra, mà nồng độ này thấp. Vì vậy phản ứng diễn ra chậm ở mọi nơi. Hãy so với thiết bị phản ứng dòng đẩy, gọi tắt là pê ép e theo tiếng Anh. Đây cũng là một khối trong process-sim-lab. Trong thiết bị dòng đẩy, chất lỏng di chuyển như một pít tông. Nồng độ giảm dần dọc theo ống. Độ chuyển hoá của nó bằng một trừ e mũ âm k tau. Trên biểu đồ này, hãy nhìn vào số Damkohler bằng năm. Bình khuấy trộn đạt tám mươi ba phần trăm. Thiết bị dòng đẩy đạt khoảng chín mươi chín phẩy ba phần trăm.

**Narrator** (series): Có một cách ở giữa, đó là nhiều bình khuấy trộn nối tiếp nhau. Chia cùng tổng số Damkohler bằng năm cho N bình giống nhau. Hai bình cho khoảng chín mươi mốt phẩy tám phần trăm. Ba bình cho khoảng chín mươi tư phẩy bảy phần trăm. Khi N tăng, chuỗi bình tiến dần tới kết quả của dòng đẩy. Bài kiểm thử của thiết bị dòng đẩy kiểm tra đúng giới hạn này. Trong thực tế, người ta cân nhắc giữa số bình và chi phí của mỗi bình. Mô hình đơn giản này giúp so sánh các phương án đó rất nhanh.

## Trong một sơ đồ công nghệ

**Guest** (ask-flowsheet): Tôi có thể đặt bình khuấy trộn vào trong một quá trình lớn hơn không?

**Narrator** (flowsheet): Được chứ. Trong sơ đồ công nghệ, loại khối này có tên là cstr. Nó có một dòng vào và một dòng ra. Ví dụ mẫu của process-sim-lab nối bốn khối với nhau. Đó là bộ trộn, bình khuấy trộn, bình tách pha và bộ chia xả. Một phần pha hơi từ bình tách quay lại bộ trộn. Đó là dòng hồi lưu. Bộ giải cắt dòng hồi lưu đó ra. Rồi nó lặp cho tới khi các lưu lượng không còn thay đổi.

**Narrator** (flowsheet-numbers): Trong ví dụ đó, hằng số tốc độ là không phẩy không hai ba trên giây. Thể tích bình là hai mét khối. Lưu lượng là không phẩy không hai mét khối trên giây. Vậy tau bằng một trăm giây, và số Damkohler bằng hai phẩy ba. Độ chuyển hoá trong một lượt là hai phẩy ba chia ba phẩy ba. Tức là khoảng bảy mươi phần trăm. Dòng hồi lưu đưa phần A chưa phản ứng quay lại. Năm phẩy sáu mol A mỗi giây đi vào hệ thống. Bốn phẩy sáu mol trong số đó phản ứng. Vậy cả hệ thống chuyển hoá được khoảng tám mươi hai phần trăm.

**Narrator** (agent): Khối này cũng là một công cụ cho trợ lý AI. Tầng agent của process-sim-lab cung cấp nó với tên công cụ là cstr. Mô hình ngôn ngữ gọi đúng hàm này, thay vì tự đoán con số. Một trong các ca đánh giá chính là ví dụ trong video này. Ca đó chờ thời gian lưu năm trăm giây. Nó cũng chờ số Damkohler bằng năm, và độ chuyển hoá năm phần sáu.

## Giới hạn và tóm tắt

**Guest** (ask-limits): Vậy mô hình này không làm được những gì?

**Narrator** (limits): Mô hình được giữ đơn giản có chủ đích. Nó không có cân bằng năng lượng. Vì vậy một phản ứng toả nhiệt mạnh nằm ngoài phạm vi. Nó chỉ xử lý một phản ứng bậc một, không phải nhiều phản ứng. Và nó giả thiết khối lượng riêng không đổi. Với những trường hợp đó, bạn cần một mô hình lớn hơn. Nhưng để học, vài dòng công thức này đã rất hữu ích. Chúng cũng giúp bạn kiểm tra nhanh một thiết kế bằng tay.

**Narrator** (recap): Chúng ta tóm tắt lại. Tau bằng thể tích chia cho lưu lượng. Số Damkohler bằng k nhân tau. Độ chuyển hoá bằng số Damkohler chia cho một cộng chính nó. Khuấy trộn hoàn toàn làm một bình kém hơn thiết bị dòng đẩy. Nhiều bình nối tiếp giúp thu hẹp khoảng cách đó. Chỉ với ba công thức này, bạn đã kiểm tra nhanh được một thiết kế. Hãy nhớ tính tau trước, rồi tính số Damkohler, rồi mới tính độ chuyển hoá.

**Narrator** (outro): Mã nguồn, bài kiểm thử và tài liệu đều được mở công khai. Bạn có thể tìm chúng trong kho process-sim-lab trên GitHub. Bạn hãy thử đổi thể tích bình trong ví dụ. Hãy đoán độ chuyển hoá trước khi chạy. Cảm ơn các bạn đã xem.
