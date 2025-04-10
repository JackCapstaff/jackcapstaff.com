<?php
use PHPMailer\PHPMailer\PHPMailer;
use PHPMailer\PHPMailer\Exception;

require 'PHPMailer/src/Exception.php';
require 'PHPMailer/src/PHPMailer.php';
require 'PHPMailer/src/SMTP.php';

// Get form data (after validation and sanitization)
$name = $_POST['demo-name'];
$email = $_POST['demo-email'];
$message = $_POST['demo-message'];

$mail = new PHPMailer(true);

try {
    // Server settings
    $mail->SMTPDebug = 3;                      // Enable verbose debug output
    $mail->isSMTP();                                            // Send using SMTP
    $mail->Host       = 'smtp.zoho.com';                    // Set the SMTP server to send through
    $mail->SMTPAuth   = true;                                   // Enable SMTP authentication
    $mail->Username   = 'noreply@jackcapstaff.com';                     // SMTP username
    $mail->Password   = 'RSUV Y523 k6hQ';                               // SMTP password (USE APP PASSWORD!)
    $mail->SMTPSecure = PHPMailer::ENCRYPTION_STARTTLS;         // Enable TLS encryption
    $mail->Port       = 587;                                    // TCP port to connect to

    // Recipients
    $mail->setFrom('noreply@jackcapstaff.com', 'Web Form');
    $mail->addAddress('jack@jackcapstaff.com', 'You');

    // Content
    $mail->isHTML(true);
    $mail->Subject = 'New message from your website';
    $mail->Body    = "You have received a new message from your website:<br><br>Name: " . htmlspecialchars($name) . "<br>Email: " . htmlspecialchars($email) . "<br>Message: " . htmlspecialchars($message);
    $mail->AltBody = "You have received a new message from your website:\n\nName: " . $name . "\nEmail: " . $email . "\nMessage: " . $message;

    $mail->send();
    echo 'Message has been sent';
} catch (Exception $e) {
    echo "Message could not be sent. Mailer Error: {$mail->ErrorInfo}";
}
?>